#!/usr/bin/env python3
"""
Interface configuration for cEOS switches and Linux hosts (three-ceos-linux lab).

Switch data-plane (Containerlab port -> EOS EthernetN):
  sw1:eth1 <-> sw2:eth1   10.1.1.0/24   (sw1 .2, sw2 .3)
  sw1:eth2 <-> sw3:eth1   10.10.10.0/24 (sw1 .2, sw3 .3)
  sw2:eth2 <-> sw3:eth2   10.20.20.0/24 (sw2 .2, sw3 .3)
  sw2:eth3 <-> host1:eth1  10.30.30.0/24 (sw2 Ethernet3 .2, host1 .1)
  sw3:eth3 <-> host2:eth1  10.40.40.0/24 (sw3 Ethernet3 .2, host2 .1)

Descriptions on switches: ``<clab-port>-<peer>`` (e.g. eth1-Switch2, eth3-host1).

Deploy order:
  linux_host_script.py -> bootstrap_config.py --all -> this script

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

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from netmiko import ConnectHandler
except ImportError:
    print("error: pip install netmiko (use .venv/bin/pip install netmiko)", file=sys.stderr)
    raise SystemExit(1) from None

from bootstrap_config import (  # noqa: E402
    _device_params,
    _ensure_privileged,
    _inspect_ips,
    _linux_containers_in_lab,
    _log,
    _preflight_lab,
    _send_config_lines,
)
from lab_topology import DEFAULT_TOPO, LINUX_HOST_DATA, TOPO_WITH_HOSTS  # noqa: E402

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


def _desc_peer(clab_iface: str, peer_label: str) -> str:
    """Description toward a Linux host or other non-switch peer (e.g. eth3-host1)."""
    return f"{clab_iface}-{peer_label}"


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
        *_iface_lines("Ethernet3", "10.30.30.2/24", _desc_peer("eth3", "host1")),
    ],
    "sw3": [
        "ip routing",
        *_iface_lines("Ethernet1", "10.10.10.3/24", _desc("eth1", "sw1")),
        *_iface_lines("Ethernet2", "10.20.20.3/24", _desc("eth2", "sw2")),
        *_iface_lines("Ethernet3", "10.40.40.2/24", _desc_peer("eth3", "host2")),
    ],
}


def _inspect_linux_container_map(topo: Path) -> Dict[str, str]:
    """Map host1/host2 -> docker container name (e.g. clab-three-ceos-linux-host1)."""
    lab_bin = shutil.which("clab") or shutil.which("containerlab")
    if not lab_bin:
        raise RuntimeError("clab/containerlab not on PATH")
    raw = subprocess.check_output([lab_bin, "inspect", "-t", str(topo), "-f", "json"], text=True)
    data = json.loads(raw)
    out: Dict[str, str] = {}
    for _lab, containers in data.items():
        for c in containers:
            name = str(c.get("name") or "")
            for key in LINUX_HOST_DATA:
                if name.endswith(f"-{key}"):
                    out[key] = name
    return out


def _docker_exec(container: str, argv: List[str], *, verbose: bool) -> str:
    cmd = ["docker", "exec", container, *argv]
    _log(verbose, f"  {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker exec failed on {container}: {' '.join(argv)}\n{result.stderr or result.stdout}"
        )
    return result.stdout


def _configure_linux_host(container: str, host_key: str, *, verbose: bool) -> None:
    """Assign data-plane IPv4 on host eth1 (toward switch Ethernet3)."""
    iface, cidr = LINUX_HOST_DATA[host_key]
    print(f"\n{'=' * 60}\n{host_key} ({container}) — Linux data-plane {iface} {cidr}\n{'=' * 60}", file=sys.stderr)
    _docker_exec(container, ["ip", "link", "set", iface, "up"], verbose=verbose)
    _docker_exec(container, ["ip", "addr", "flush", "dev", iface], verbose=verbose)
    _docker_exec(container, ["ip", "addr", "add", cidr, "dev", iface], verbose=verbose)
    out = _docker_exec(container, ["ip", "-4", "addr", "show", "dev", iface], verbose=verbose)
    print(f"\n--- {host_key} ({container}) : ip addr ---\n{out}\n")


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
        description="Configure cEOS data interfaces and Linux host eth1 (three-ceos-linux lab).",
    )
    p.add_argument("-t", "--topo", type=Path, default=DEFAULT_TOPO)
    p.add_argument("--hosts", nargs=3, metavar=("SW1", "SW2", "SW3"))
    p.add_argument("--inspect", action="store_true", help="Use clab inspect for switch mgmt IPs")
    p.add_argument(
        "--switches",
        nargs="+",
        choices=["sw1", "sw2", "sw3"],
        metavar="SW",
        help="Configure only these switches (default: all)",
    )
    p.add_argument(
        "--skip-linux-hosts",
        action="store_true",
        help="Do not configure host1/host2 data-plane IPs (switches only)",
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

    if not args.skip_linux_hosts and args.topo.resolve() == TOPO_WITH_HOSTS.resolve():
        have = set(_linux_containers_in_lab(args.topo))
        if not {"host1", "host2"}.issubset(have):
            print(
                "warning: host1/host2 not in lab; skipping Linux host IPs "
                "(deploy with linux_host_script.py)",
                file=sys.stderr,
            )
        else:
            containers = _inspect_linux_container_map(args.topo)
            for host_key in ("host1", "host2"):
                try:
                    _configure_linux_host(containers[host_key], host_key, verbose=args.verbose)
                except Exception as exc:
                    print(f"error configuring {host_key}: {exc}", file=sys.stderr)
                    return 1
    elif not args.skip_linux_hosts and not args.switches:
        print(
            "note: use --topo three-ceos-linux.clab.yml (default) for host1/host2 wiring",
            file=sys.stderr,
        )

    print("\nDone.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
