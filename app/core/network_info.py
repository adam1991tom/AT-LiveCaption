"""Local network address discovery -- purely informational, so the operator
can see which address(es) to give another device on the network. No network
calls of any kind, just reading this machine's own interface list."""
from __future__ import annotations

import socket

import psutil

_EXCLUDE_NAME_SUBSTRINGS = ("virtualbox", "vmware", "hyper-v", "vethernet", "loopback")


def list_lan_addresses() -> list[str]:
    addresses: set[str] = set()
    for name, addrs in psutil.net_if_addrs().items():
        if any(s in name.lower() for s in _EXCLUDE_NAME_SUBSTRINGS):
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith(("127.", "169.254.")):
                addresses.add(addr.address)
    return sorted(addresses)
