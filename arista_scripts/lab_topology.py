"""Shared Containerlab topology paths and Linux host data-plane settings."""

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# Switches only (legacy).
TOPO_SWITCHES = SCRIPT_DIR / "three-ceos.clab.yml"

# Switches + host1/host2 (linux_host_script.py).
TOPO_WITH_HOSTS = SCRIPT_DIR / "three-ceos-linux.clab.yml"

# Default for bootstrap / interface_configuration after adding Linux hosts.
DEFAULT_TOPO = TOPO_WITH_HOSTS

# Linux host L3 toward switches (host .1, switch .2 on each /24).
LINUX_HOST_DATA: dict[str, tuple[str, str]] = {
    "host1": ("eth1", "10.30.30.1/24"),  # -> sw2 Ethernet3 10.30.30.2
    "host2": ("eth1", "10.40.40.1/24"),  # -> sw3 Ethernet3 10.40.40.2
}

LAB_WORKFLOW = """
Lab with Linux hosts (recommended order):
  1. python arista_scripts/linux_host_script.py
  2. python arista_scripts/bootstrap_config.py -v --all
  3. python arista_scripts/interface_configuration.py -v
"""
