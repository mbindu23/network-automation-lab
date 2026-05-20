#!/usr/bin/env python3
"""
Deploy two lightweight Linux hosts and wire them to cEOS switches with Containerlab.

Topology (lab name default: three-ceos-linux):
  host1:eth1 <-> sw2:eth3   (EOS Ethernet3 on sw2)
  host2:eth1 <-> sw3:eth3   (EOS Ethernet3 on sw3)

Also includes the three-ceos switch mesh so the lab is self-contained.

Requires: clab/containerlab on PATH, Docker images for cEOS and Linux.

If an older three-ceos lab is running, destroy it first or use a different --lab-name:

  clab destroy -t arista_scripts/three-ceos.clab.yml   # if old lab is running
  .venv/bin/python arista_scripts/linux_host_script.py
  .venv/bin/python arista_scripts/bootstrap_config.py -v --all
  .venv/bin/python arista_scripts/interface_configuration.py -v
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from lab_topology import TOPO_WITH_HOSTS

SCRIPT_DIR = _SCRIPT_DIR
DEFAULT_TOPO = TOPO_WITH_HOSTS


def _clab() -> Optional[str]:
    for name in ("clab", "containerlab"):
        if shutil.which(name):
            return name
    return None


def _topology_yaml(
    lab_name: str,
    *,
    ceos_image: str,
    linux_image: str,
) -> str:
    return f"""name: {lab_name}

topology:
  nodes:
    sw1:
      kind: arista_ceos
      image: {ceos_image}
    sw2:
      kind: arista_ceos
      image: {ceos_image}
    sw3:
      kind: arista_ceos
      image: {ceos_image}
    host1:
      kind: linux
      image: {linux_image}
    host2:
      kind: linux
      image: {linux_image}

  links:
    # switch mesh (three-ceos)
    - endpoints: ["sw1:eth1", "sw2:eth1"]
    - endpoints: ["sw1:eth2", "sw3:eth1"]
    - endpoints: ["sw2:eth2", "sw3:eth2"]
    # Linux hosts -> switch eth3 (EOS Ethernet3)
    - endpoints: ["host1:eth1", "sw2:eth3"]
    - endpoints: ["host2:eth1", "sw3:eth3"]
"""


def main() -> int:
    p = argparse.ArgumentParser(
        description="Deploy two Linux hosts attached to sw2:eth3 and sw3:eth3.",
    )
    p.add_argument("--lab-name", default="three-ceos-linux", help="Containerlab topology name")
    p.add_argument("--ceos-image", default="ceos:latest", help="cEOS image for sw1–sw3")
    p.add_argument(
        "--linux-image",
        default="ghcr.io/srl-labs/alpine",
        help="Lightweight Linux image (default: srl-labs alpine)",
    )
    p.add_argument(
        "--topo-file",
        type=Path,
        default=None,
        help=f"Write topology here (default: {DEFAULT_TOPO})",
    )
    p.add_argument("--dry-run", action="store_true", help="Only write YAML, do not deploy")
    args = p.parse_args()

    topo = args.topo_file or (SCRIPT_DIR / f"{args.lab_name}.clab.yml")
    topo.write_text(
        _topology_yaml(args.lab_name, ceos_image=args.ceos_image, linux_image=args.linux_image),
        encoding="utf-8",
    )
    print(f"Wrote {topo}", file=sys.stderr)
    print(
        "Links: host1 -> sw2:eth3 (Ethernet3), host2 -> sw3:eth3 (Ethernet3)",
        file=sys.stderr,
    )

    if args.dry_run:
        return 0

    bin_name = _clab()
    if not bin_name:
        print("error: install Containerlab (`clab` or `containerlab`) on PATH", file=sys.stderr)
        return 1

    cmd = [bin_name, "deploy", "-t", str(topo)]
    print(" ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
