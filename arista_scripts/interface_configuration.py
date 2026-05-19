#!/usr/bin/env python3
"""
Interface configuration for all three cEOS switches (three-ceos lab).

Data-plane layout (Containerlab linux port -> EOS EthernetN):
  sw1:eth1 <-> sw2:eth1   10.1.1.0/24   (sw1 .2, sw2 .3)
  sw1:eth2 <-> sw3:eth1   10.10.10.0/24 (sw1 .2, sw3 .3)
  sw2:eth2 <-> sw3:eth2   10.20.20.0/24 (sw2 .2, sw3 .3)

Interface descriptions use format ``<clab-port>-<peer-hostname>`` (e.g. eth1-Switch2).

Requires lab running, bootstrap mgmt (172.20.20.11–13), and: pip install netmiko
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

try:
    from netmiko import ConnectHandler
except ImportError:
    print("error: pip install netmiko (use .venv/bin/pip install netmiko)", file=sys.stderr)
    raise SystemExit(1) from None

from bootstrap_config import (  # noqa: E402
    DEFAULT_TOPO,
    _device_params,
    _ensure_privileged,
    _inspect_ips,
    _log,
    _send_config_lines,
)

DEFAULT_MGMT: Dict[str, str] = {
    "sw1": "172.20.20.11",
    "sw2": "172.20.20.12",
    "sw3": "172.20.20.13",
}

SHOW_BRIEF = "show ip interface brief"

# Bootstrap hostnames (bootstrap_config.py).
HOSTNAMES = {"sw1": "Switch1", "sw2": "Switch2", "sw3": "Switch3"}


def _desc(clab_iface: str, peer_sw: str) -> str:
    """Description format: interface-name-hostname (e.g. eth1-Switch2)."""
    return f"{clab_iface}-{HOSTNAMES[peer_sw]}"


def _iface_lines(eos_name: str, ip_cidr: str, description: str) -> List[str]:
    return [
        f"interface {eos_name}",
        "   no switchport",
        f"   description {description}",
        f"   ip address {ip_cidr}",
    ]


# Full routed interface config per switch (inside configure terminal).
INTERFACE_CONFIG: Dict[str, List[str]] = {
    "sw1": [
        "ip routing",
        *_iface_lines("Ethernet1", "10.1.1.2/24", _desc("eth1", "sw2")),
        *_iface_lines("Ethernet2", "10.10.10.2/24", _desc("eth2", "sw3")),
    ],
    "sw2": [
        "ip routing",
        *_iface_lines("Ethernet1", "10.1.1.3/24", _desc("eth1", "sw1")),
        *_iface_lines("Ethernet2", "10.20.20.2/24", _desc("eth2", "sw3")),
    ],
    "sw3": [
        "ip routing",
        *_iface_lines("Ethernet1", "10.10.10.3/24", _desc("eth1", "sw1")),
        *_iface_lines("Ethernet2", "10.20.20.3/24", _desc("eth2", "sw2")),
    ],
}


def _preflight_lab(topo: Path) -> None:
    lab_bin = shutil.which("clab") or shutil.which("containerlab")
    if not lab_bin:
        return
    raw = subprocess.run(
        [lab_bin, "inspect", "-t", str(topo), "-f", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if raw.returncode != 0:
        return
    data = json.loads(raw.stdout)
    down = [
        str(c.get("name") or "unknown")
        for _lab, containers in data.items()
        for c in containers
        if str(c.get("state") or "").lower() != "running"
    ]
    if down:
        raise RuntimeError(
            f"lab nodes not running: {', '.join(down)}\n"
            f"  clab deploy -t {topo} --reconfigure\n"
            "  .venv/bin/python arista_scripts/bootstrap_config.py -v --all"
        )


def _configure_switch(sw: str, host: str, lines: List[str], *, verbose: bool) -> None:
    print(f"\n{'=' * 60}\n{sw} ({host}) — applying interface configuration\n{'=' * 60}", file=sys.stderr)
    print(f"  → SSH {host}", file=sys.stderr, flush=True)

    with ConnectHandler(**_device_params(host)) as conn:
        conn.clear_buffer()
        _ensure_privileged(conn, verbose)
        _send_config_lines(
            conn,
            ["configure terminal", *lines, "end"],
            verbose=verbose,
            label=sw,
            read_timeout=90,
        )
        _log(verbose, f"  {SHOW_BRIEF}...")
        brief = conn.send_command_timing(
            SHOW_BRIEF,
            last_read=2.0,
            read_timeout=60,
            strip_prompt=False,
            strip_command=False,
        )

    with ConnectHandler(**_device_params(host)) as conn:
        conn.clear_buffer()
        _ensure_privileged(conn, verbose)
        conn.send_command_timing("write memory", last_read=3.0, read_timeout=120)

    print(f"\n--- {sw} ({host}) : {SHOW_BRIEF} ---\n{brief}\n")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Configure data interfaces on sw1, sw2, and sw3 (cEOS three-ceos lab).",
    )
    p.add_argument("-t", "--topo", type=Path, default=DEFAULT_TOPO)
    p.add_argument("--hosts", nargs=3, metavar=("SW1", "SW2", "SW3"))
    p.add_argument("--inspect", action="store_true", help="Use clab inspect for mgmt IPs")
    p.add_argument(
        "--switches",
        nargs="+",
        choices=["sw1", "sw2", "sw3"],
        metavar="SW",
        help="Configure only these switches (default: all)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    try:
        _preflight_lab(args.topo)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.hosts:
        hosts = {"sw1": args.hosts[0], "sw2": args.hosts[1], "sw3": args.hosts[2]}
    elif args.inspect:
        hosts = _inspect_ips(args.topo)
    else:
        hosts = dict(DEFAULT_MGMT)

    targets = args.switches if args.switches else ["sw1", "sw2", "sw3"]

    for sw in targets:
        try:
            _configure_switch(sw, hosts[sw], INTERFACE_CONFIG[sw], verbose=args.verbose)
        except Exception as exc:
            print(f"error configuring {sw}: {exc}", file=sys.stderr)
            return 1

    print("\nDone.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
