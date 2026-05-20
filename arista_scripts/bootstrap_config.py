#!/usr/bin/env python3
"""
Apply baseline configuration to the three cEOS nodes (sw1–sw3).

For each switch:
  - hostname Switch1 / Switch2 / Switch3 (EOS hostnames cannot contain spaces)
  - static IPv4 on Management0 (description: management) + default route
  - management SSH + HTTPS (management api http-commands)
  - local user arista.net with secret arista (hashed on-device)

Linux hosts (host1, host2) are deployed by linux_host_script.py; they are not
configured here. After bootstrap, run interface_configuration.py for switch
and host data-plane IPs.

Recommended lab: three-ceos-linux (switches + host1/host2). Deploy order:
  linux_host_script.py -> bootstrap_config.py --all -> interface_configuration.py

Requires:
  - Lab deployed; SSH reachable as admin (default Containerlab: admin / admin).
  - pip install netmiko
  - clab or containerlab on PATH (for automatic IP lookup), unless you pass --hosts.

By default only sw1 is configured; use --switches sw2 sw3 or --all for more nodes.

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

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from netmiko import ConnectHandler
except ImportError:
    print(
        "error: netmiko is not installed.\n"
        "  Ubuntu/Debian blocks system-wide pip (PEP 668). Use a virtual env:\n"
        "    sudo apt install python3.14-venv   # or: sudo apt install python3-venv\n"
        "    cd ~/Automation && python3 -m venv .venv\n"
        "    .venv/bin/pip install netmiko\n"
        "    .venv/bin/python arista_scripts/bootstrap_config.py\n",
        file=sys.stderr,
    )
    raise SystemExit(1) from None


from lab_topology import DEFAULT_TOPO, TOPO_SWITCHES, TOPO_WITH_HOSTS  # noqa: E402

SCRIPT_DIR = _SCRIPT_DIR

# Management0 appears as Management0 in brief; Ma0 is the usual short name.
SHOW_MGMT_BRIEF = "show ip interface brief | include Management0"


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


def _linux_containers_in_lab(topo: Path) -> List[str]:
    """Return clab container names for host1/host2 when present."""
    lab_bin = _resolve_clab()
    if not lab_bin:
        return []
    raw = subprocess.check_output([lab_bin, "inspect", "-t", str(topo), "-f", "json"], text=True)
    data: Dict[str, Any] = json.loads(raw)
    found: List[str] = []
    for _lab, containers in data.items():
        for c in containers:
            name = str(c.get("name") or "")
            if name.endswith(("-host1", "-host2")):
                found.append(name.rsplit("-", 1)[-1])
    return found


def _preflight_lab(topo: Path) -> None:
    """Ensure switches (and Linux hosts when using three-ceos-linux topo) are running."""
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
            "  .venv/bin/python arista_scripts/linux_host_script.py\n"
            "  .venv/bin/python arista_scripts/bootstrap_config.py -v --all"
        )
    if topo.resolve() == TOPO_WITH_HOSTS.resolve():
        have = set(_linux_containers_in_lab(topo))
        need = {"host1", "host2"}
        if not need.issubset(have):
            missing = ", ".join(sorted(need - have))
            raise RuntimeError(
                f"topology expects Linux hosts but clab inspect missing: {missing}\n"
                "  .venv/bin/python arista_scripts/linux_host_script.py"
            )


def _mgmt_host(mgmt_cidr: str) -> str:
    return mgmt_cidr.split("/")[0].strip()


def _build_config(hostname: str, mgmt_cidr: str, gateway: str) -> tuple[List[str], List[str]]:
    """Return (main lines, Management0 lines). Ma0 is applied last — it often drops SSH."""
    main = [
        f"hostname {hostname}",
        "aaa authorization exec default local",
        "management ssh",
        "   idle-timeout 0",
        "management api http-commands",
        "   protocol https",
        "   no shutdown",
        "username arista.net privilege 15 role network-admin secret arista",
    ]
    mgmt0 = [
        "interface Management0",
        "   description management",
        f"   ip address {mgmt_cidr}",
        f"ip route 0.0.0.0/0 {gateway}",
    ]
    return main, mgmt0


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


def _device_params(host: str) -> Dict[str, Any]:
    return {
        "device_type": "arista_eos",
        "host": host,
        "username": "admin",
        "password": "admin",
        "secret": "admin",
        "port": 22,
        "timeout": 180,
        "conn_timeout": 90,
        "banner_timeout": 120,
        "auth_timeout": 90,
        "session_timeout": 120,
        "fast_cli": False,
        "global_delay_factor": 2,
    }


def _send_config_lines(
    conn: Any,
    lines: List[str],
    *,
    verbose: bool,
    label: str,
    read_timeout: float = 120,
) -> List[str]:
    chunks: List[str] = []
    for i, cmd in enumerate(lines, start=1):
        _log(verbose, f"    [{label} {i}/{len(lines)}] {cmd[:72]}{'…' if len(cmd) > 72 else ''}")
        try:
            chunk = conn.send_command_timing(
                cmd,
                last_read=2.0,
                read_timeout=read_timeout,
                strip_prompt=False,
                strip_command=False,
            )
        except Exception as exc:
            # Management0 address change often resets the SSH session mid-command.
            if "Management0" in cmd or "ip route" in cmd:
                _log(verbose, f"    (session may have dropped after {cmd!r}: {exc})")
                break
            raise
        if "% Invalid input" in chunk:
            raise RuntimeError(f"EOS rejected command {cmd!r}:\n{chunk}")
        chunks.append(chunk)
    return chunks


def _push(
    host: str,
    main_commands: List[str],
    mgmt0_commands: List[str],
    mgmt_cidr: str,
    *,
    switch: str = "",
    verbose: bool = False,
) -> None:
    """Push config using delay-based I/O; reconnect after Ma0 IP change to save config."""
    new_host = _mgmt_host(mgmt_cidr)
    chunks: List[str] = []
    print(
        f"  → SSH {host}: connecting (cEOS can take 1–3 minutes before the first prompt; use -v for steps)",
        file=sys.stderr,
        flush=True,
    )
    _log(verbose, f"  SSH {host}: opening session...")
    with ConnectHandler(**_device_params(host)) as conn:
        conn.clear_buffer()
        chunks.append(_ensure_privileged(conn, verbose))
        _log(verbose, "  configure terminal + baseline...")
        chunks.extend(
            _send_config_lines(
                conn,
                ["configure terminal", *main_commands, *mgmt0_commands, "end"],
                verbose=verbose,
                label="cfg",
                read_timeout=60,
            )
        )

    save_host = new_host if new_host != host else host
    if new_host != host:
        print(
            f"  → SSH {save_host}: reconnecting to save (Management0 moved off {host})",
            file=sys.stderr,
            flush=True,
        )
    _log(verbose, f"  SSH {save_host}: write memory...")
    with ConnectHandler(**_device_params(save_host)) as conn:
        conn.clear_buffer()
        chunks.append(_ensure_privileged(conn, verbose))
        chunks.append(
            conn.send_command_timing(
                "write memory",
                last_read=3.0,
                read_timeout=120,
                strip_prompt=False,
                strip_command=False,
            )
        )
        _log(verbose, f"  {SHOW_MGMT_BRIEF}...")
        ma0_brief = conn.send_command_timing(
            SHOW_MGMT_BRIEF,
            last_read=2.0,
            read_timeout=60,
            strip_prompt=False,
            strip_command=False,
        )

    label = switch or save_host
    print(f"\n--- {label} ({save_host}) : {SHOW_MGMT_BRIEF} ---\n{ma0_brief}\n")
    if verbose:
        print("\n".join(chunks))


def main() -> int:
    p = argparse.ArgumentParser(description="Bootstrap (baseline) config for Arista cEOS switches.")
    p.add_argument(
        "-t",
        "--topo",
        type=Path,
        default=DEFAULT_TOPO,
        help=f"Topology for inspect (default: three-ceos-linux; legacy: {TOPO_SWITCHES.name})",
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
    p.add_argument(
        "--all",
        action="store_true",
        help="Configure sw1, sw2, and sw3 (default: sw1 only)",
    )
    p.add_argument(
        "--switches",
        nargs="+",
        choices=["sw1", "sw2", "sw3"],
        metavar="SW",
        help="Configure only these nodes (e.g. --switches sw2 sw3)",
    )
    args = p.parse_args()

    try:
        _preflight_lab(args.topo)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

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
    if args.switches:
        wanted = set(args.switches)
        plan = [entry for entry in plan if entry[0] in wanted]
    elif not args.all:
        plan = plan[:1]

    for key, hname, mgmt in plan:
        print(f"\n=== {key} ({hosts[key]}): hostname {hname}, Ma0 {mgmt}, gw {args.gateway} ===")
        main_cfg, mgmt0_cfg = _build_config(hname, mgmt, args.gateway)
        try:
            _push(hosts[key], main_cfg, mgmt0_cfg, mgmt, switch=key, verbose=args.verbose)
        except Exception as exc:  # noqa: BLE001 — surface useful failure to operator
            print(f"error configuring {key}: {exc}", file=sys.stderr)
            return 1

    print(
        "\nDone (switches only). Next: interface_configuration.py for Ethernet "
        "and Linux host data-plane IPs.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
