#!/usr/bin/env python3
"""
ospf_verify.py
==============
Your first network automation script.
Connects to all 3 routers and collects OSPF information.

How to run:
    pip install netmiko
    python3 ospf_verify.py
"""

# ---------------------------------------------------------------
# IMPORTS - think of these as "loading tools" before you use them
# netmiko is a library that handles SSH connections to network devices
# ---------------------------------------------------------------
from netmiko import ConnectHandler

# ---------------------------------------------------------------
# DEVICE DEFINITIONS
# A "dictionary" in Python is like a table of key:value pairs.
# We define one dictionary per router with its connection details.
# ContainerLab assigns management IPs starting at 172.20.20.2
# Check yours with: docker inspect clab-ospf3node-R1 | grep IPAddress
# ---------------------------------------------------------------
R1 = {
    "device_type": "arista_eos",   # tells Netmiko what kind of device this is
    "host": "172.20.20.2",         # management IP - update if different on your machine
    "username": "admin",
    "password": "admin",
    "port": 22,                    # SSH port
}

R2 = {
    "device_type": "arista_eos",
    "host": "172.20.20.3",
    "username": "admin",
    "password": "admin",
    "port": 22,
}

R3 = {
    "device_type": "arista_eos",
    "host": "172.20.20.4",
    "username": "admin",
    "password": "admin",
    "port": 22,
}

# A "list" in Python holds multiple items in order.
# We put all 3 device dicts into a list so we can loop through them.
devices = [R1, R2, R3]

# ---------------------------------------------------------------
# COMMANDS TO RUN ON EACH DEVICE
# Another list - this time of strings (text commands)
# ---------------------------------------------------------------
commands = [
    "show ip ospf neighbor",        # who am I peered with?
    "show ip ospf database summary", # what LSAs are in my LSDB?
    "show ip route ospf",           # what routes did OSPF install?
]

# ---------------------------------------------------------------
# MAIN LOGIC
# "for" loop = do something for each item in a list
# ---------------------------------------------------------------
for device in devices:
    print("\n" + "=" * 60)
    print(f"Connecting to: {device['host']}")   # f-string: {} inserts a variable
    print("=" * 60)

    # "try/except" = attempt something, catch errors gracefully
    # Without this, one failed connection crashes the whole script
    try:
        # ConnectHandler opens the SSH session
        # "with" means: auto-close the connection when done (good practice)
        with ConnectHandler(**device) as ssh:
            # **device "unpacks" the dictionary into keyword arguments
            # same as writing: ConnectHandler(device_type="arista_eos", host="...", ...)

            print(f"  Connected! Running commands...\n")

            for cmd in commands:
                print(f"  >>> {cmd}")
                # send_command runs one command and returns the output as a string
                output = ssh.send_command(cmd)
                print(output)
                print()   # blank line between commands

    except Exception as e:
        # "e" holds the error message if something goes wrong
        print(f"  ERROR connecting to {device['host']}: {e}")
        print("  Check: is ContainerLab running? Is the IP correct?")

print("\nDone! All devices checked.")
