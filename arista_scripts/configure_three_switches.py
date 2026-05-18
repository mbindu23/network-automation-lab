#!/usr/bin/env python3
"""
Apply baseline configuration to the three cEOS nodes (sw1–sw3).

For each switch:
  - hostname Switch1 / Switch2 / Switch3 (EOS hostnames cannot contain spaces)
  - static IPv4 on Management0 + default route (Containerlab-style gateway)
  - management SSH + HTTPS (management api http-commands)
  - local user arista.net with secret arista (hashed on-device)

Requires:
  - Lab deployed; SSH reachable as admin (default Containerlab: admin / admin).
  - pip install netmiko
  - clab or containerlab on PATH (for automatic IP lookup), unless you pass --hosts.

Note: Changing Management0 addressing can drop the SSH session before the script
finishes. If that happens, re-run using the new addresses via --hosts.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from netmiko import ConnectHandler
except ImportError:
    print(
        "error: netmiko is not installed.\n"
        "  Ubuntu/Debian blocks system-wide pip (PEP 668). Use a virtual env:\n"
        "    sudo apt install python3.14-venv   # or: sudo apt install python3-venv\n"
        "    cd ~/Automation && python3 -m venv .venv\n"
        "    .venv/bin/pip install netmiko\n"
        "    .venv/bin/python arista_scripts/configure_three_switches.py\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TOPO = SCRIPT_DIR / "three-ceos.clab.yml"


def _resolve_clab() -> Optional[str]:
    for name in ("clab", "containerlab"):
        if shutil.which(name):
            return name
    return None


def _inspect_ips(topo: Path) -> Dict[str, str]:
    """Map sw1/sw2/sw3 -> current mgmt IPv4 (no prefix length)."""
    lab_bin = _resolve_clab()
    if not lab_bin:
        raise RuntimeError("clab/containerlab not on PATH (needed for inspect)")
    raw = subprocess.check_output([lab_bin, "inspect", "-t", str(topo), "-f", "json"], text=True)
    data: Dict[str, Any] = json.loads(raw)
    out: Dict[str, str] = {}
    for _lab, containers in data.items():
        for c in containers:
            name = str(c.get("name") or "")
            if not name.endswith(("-sw1", "-sw2", "-sw3")):
                continue
            short = name.rsplit("-", 1)[-1]
            ipv4 = str(c.get("ipv4_address") or "").split("/")[0].strip()
            if ipv4:
                out[short] = ipv4
    for want in ("sw1", "sw2", "sw3"):
        if want not in out:
            raise RuntimeError(f"could not find IPv4 for {want} in clab inspect output")
    return out


def _build_config(hostname: str, mgmt_cidr: str, gateway: str) -> List[str]:
    """EOS config lines (without configure terminal / end — Netmiko handles mode)."""
    # Hostnames cannot contain spaces in EOS; Switch1 == “Switch 1” style label.
    return [
        f"hostname {hostname}",
        "aaa authorization exec default local",
        "management ssh",
        "   idle-timeout 0",
        "management api http-commands",
        "   protocol https default",
        "   no shutdown",
        "interface Management0",
        f"   ip address {mgmt_cidr}",
        f"ip route 0.0.0.0/0 {gateway}",
        "username arista.net privilege 15 role network-admin secret arista",
    ]


def _log(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def _ensure_privileged(conn: Any, verbose: bool) -> str:
    """Enter enable mode. cEOS SSH often lands at ``hostname>`` (exec), not ``#``."""
    _log(verbose, "  enable (privileged exec)...")
    out = conn.send_command_timing("enable", last_read=2.0, read_timeout=60)
    prompt = conn.find_prompt(delay_factor=2)
    if prompt.rstrip().endswith("#"):
        return out
    # Some images prompt for an enable password (lab default is often same as login).
    _log(verbose, "  enable password...")
    out += conn.send_command_timing("admin", last_read=2.0, read_timeout=30)
    prompt = conn.find_prompt(delay_factor=2)
    if not prompt.rstrip().endswith("#"):
        raise RuntimeError(
            f"still not in privileged mode (prompt {prompt!r}). "
            "Config was not applied — only run from exec (>) if you see '% Invalid input'."
        )
    return out


def _push(host: str, commands: List[str], *, verbose: bool = False) -> None:
    """Push config using delay-based I/O.

    Netmiko's default Arista ``config_mode()`` regex often fails on cEOS when SSH
    banners / pacing differ; ``send_command_timing`` avoids strict prompt matching.
    """
    dev = {
        "device_type": "arista_eos",
        "host": host,
        "username": "admin",
        "password": "admin",
        "secret": "admin",  # enable password when the device prompts for one
        "port": 22,
        # cEOS / Containerlab can be slow to present a prompt after auth (large MOTD, CPU).
        "timeout": 180,
        "conn_timeout": 90,
        "banner_timeout": 120,
        "auth_timeout": 90,
        "session_timeout": 120,
        "fast_cli": False,
        "global_delay_factor": 2,
    }
    seq = ["configure terminal", *commands, "end", "write memory"]
    chunks: List[str] = []
    print(
        f"  → SSH {host}: connecting (cEOS can take 1–3 minutes before the first prompt; use -v for steps)",
        file=sys.stderr,
        flush=True,
    )
    _log(verbose, f"  SSH {host}: opening session...")
    with ConnectHandler(**dev) as conn:
        _log(verbose, "  connected; preparing channel...")
        conn.clear_buffer()
        chunks.append(_ensure_privileged(conn, verbose))
        _log(verbose, "  sending configuration...")
        for i, cmd in enumerate(seq, start=1):
            _log(verbose, f"    [{i}/{len(seq)}] {cmd[:72]}{'…' if len(cmd) > 72 else ''}")
            chunk = conn.send_command_timing(
                cmd,
                last_read=3.0,
                read_timeout=240,
                strip_prompt=False,
                strip_command=False,
            )
            if "% Invalid input" in chunk:
                raise RuntimeError(
                    f"EOS rejected command {cmd!r} — still not in config/privileged mode?\n{chunk}"
                )
            chunks.append(chunk)
    print("\n".join(chunks))


def main() -> int:
    p = argparse.ArgumentParser(description="Configure three Arista cEOS switches.")
    p.add_argument(
        "-t",
        "--topo",
        type=Path,
        default=DEFAULT_TOPO,
        help=f"Topology file for clab inspect (default: {DEFAULT_TOPO})",
    )
    p.add_argument(
        "--gateway",
        default="172.20.20.1",
        help="IPv4 default gateway on Management0 (Containerlab default bridge GW)",
    )
    p.add_argument(
        "--sw1-ip",
        default="172.20.20.11/24",
        help="Static IPv4/prefix for sw1 Management0",
    )
    p.add_argument("--sw2-ip", default="172.20.20.12/24", help="Static IPv4/prefix for sw2 Management0")
    p.add_argument("--sw3-ip", default="172.20.20.13/24", help="Static IPv4/prefix for sw3 Management0")
    p.add_argument(
        "--hosts",
        nargs=3,
        metavar=("SW1_HOST", "SW2_HOST", "SW3_HOST"),
        help="Skip inspect; SSH targets in order sw1 sw2 sw3 (IPs or DNS names)",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print SSH progress on stderr (useful if the script appears stuck)",
    )
    args = p.parse_args()

    hosts: Dict[str, str]
    if args.hosts:
        hosts = {"sw1": args.hosts[0], "sw2": args.hosts[1], "sw3": args.hosts[2]}
    else:
        hosts = _inspect_ips(args.topo)

    plan = [
        ("sw1", "Switch1", args.sw1_ip),
        ("sw2", "Switch2", args.sw2_ip),
        ("sw3", "Switch3", args.sw3_ip),
    ]

    for key, hname, mgmt in plan:
        print(f"\n=== {key} ({hosts[key]}): hostname {hname}, Ma0 {mgmt}, gw {args.gateway} ===")
        cfg = _build_config(hname, mgmt, args.gateway)
        try:
            _push(hosts[key], cfg, verbose=args.verbose)
        except Exception as exc:  # noqa: BLE001 — surface useful failure to operator
            print(f"error configuring {key}: {exc}", file=sys.stderr)
            return 1

    print("\nDone. Verify with: show management api http-commands ; show running-config section management ssh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
