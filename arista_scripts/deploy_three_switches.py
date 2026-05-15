#!/usr/bin/env python3
"""
Spin up three Arista cEOS switches with Containerlab.

Writes a .clab.yml next to this script and runs `clab deploy` (or `containerlab`).
Requires: Docker image for cEOS (default: ceos:latest), Containerlab on PATH.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _clab() -> Optional[str]:
    for name in ("clab", "containerlab"):
        if shutil.which(name):
            return name
    return None


def _topology_yaml(lab_name: str, image: str) -> str:
    return f"""name: {lab_name}

topology:
  nodes:
    sw1:
      kind: arista_ceos
      image: {image}
    sw2:
      kind: arista_ceos
      image: {image}
    sw3:
      kind: arista_ceos
      image: {image}
"""


def main() -> int:
    p = argparse.ArgumentParser(description="Deploy three cEOS switches via Containerlab.")
    p.add_argument("--lab-name", default="three-ceos", help="Lab / topology name")
    p.add_argument("--image", default="ceos:latest", help="Docker image for each switch")
    p.add_argument(
        "--topo-file",
        type=Path,
        default=None,
        help="Topology file path (default: <script_dir>/<lab-name>.clab.yml)",
    )
    p.add_argument("--dry-run", action="store_true", help="Only write YAML, do not deploy")
    args = p.parse_args()

    here = Path(__file__).resolve().parent
    topo = args.topo_file or (here / f"{args.lab_name}.clab.yml")
    topo.write_text(_topology_yaml(args.lab_name, args.image), encoding="utf-8")
    print(f"Wrote {topo}", file=sys.stderr)

    if args.dry_run:
        return 0

    bin_name = _clab()
    if not bin_name:
        print("error: install Containerlab so `clab` or `containerlab` is on PATH", file=sys.stderr)
        return 1

    cmd = [bin_name, "deploy", "-t", str(topo)]
    print(" ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
