"""Which addresses can reach a server bound to a non-loopback address (housekeeping, Milestone 2).

Behavior spec
-------------
:func:`reachable_addresses` answers "what does a container or another machine type to reach this
server" for a bind address:

- a loopback bind (``127.0.0.0/8``, ``::1``, ``localhost``) returns nothing: only this machine can
  connect, and the caller prints nothing;
- the wildcard (``0.0.0.0``, ``::``, or empty) returns every non-loopback IPv4 address of this
  host, with its interface name when known, sorted by address;
- any other address returns just that address, because a server bound to one interface is not
  reachable on the others.

Interfaces are read with the Linux ``SIOCGIFADDR`` ioctl over ``socket.if_nameindex()``. Where
that is unavailable (Windows, macOS), the host name's IPv4 addresses and the source address of the
default route (a UDP ``connect`` to a documentation address, which sends no packet) are used
instead. Nothing here contacts the network.

:func:`describe_reachable` formats the result as the line ``openfactory run`` prints after the
register map. OpenPLC's Slave Device field takes a numeric IPv4 address, so this is the list to
choose from (docs/guides/openplc-quickstart.md, step 5).
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import struct
import sys

_SIOCGIFADDR = 0x8915
_WILDCARD = {"", "0.0.0.0", "::"}  # noqa: S104 - recognizing the wildcard, not binding it


def _is_loopback(address: str) -> bool:
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def _linux_interfaces() -> list[tuple[str, str]]:
    import fcntl  # Linux / POSIX only

    out = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for _, name in socket.if_nameindex():
            packed = struct.pack("256s", name.encode()[:15])
            try:
                result = fcntl.ioctl(s.fileno(), _SIOCGIFADDR, packed)
            except OSError:
                continue  # interface without an IPv4 address
            out.append((socket.inet_ntoa(result[20:24]), name))
    return out


def _fallback_addresses() -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.setdefault(str(info[4][0]), "")
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))  # TEST-NET-1: picks the default-route source; sends nothing
        found.setdefault(s.getsockname()[0], "default route")
    return list(found.items())


def host_ipv4_addresses() -> list[tuple[str, str]]:
    """Every non-loopback IPv4 address of this host as ``(address, interface)``, sorted."""
    pairs: list[tuple[str, str]] = []
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError, ImportError):
            pairs = _linux_interfaces()
    if not pairs:
        pairs = _fallback_addresses()
    unique = {a: n for a, n in pairs if not _is_loopback(a) and a != "0.0.0.0"}  # noqa: S104
    return sorted(unique.items(), key=lambda p: ipaddress.ip_address(p[0]))


def reachable_addresses(bind: str) -> list[tuple[str, str]]:
    """Addresses other machines or containers can use for a server bound to ``bind``."""
    if _is_loopback(bind):
        return []
    if bind in _WILDCARD:
        return host_ipv4_addresses()
    return [(bind, "")]


def describe_reachable(bind: str, port: int, addresses: list[tuple[str, str]] | None = None) -> str:
    """The startup line, or ``""`` for a loopback bind."""
    pairs = reachable_addresses(bind) if addresses is None else addresses
    if _is_loopback(bind):
        return ""
    if not pairs:
        return (
            f"reachable from containers or other machines at: no IPv4 address found (port {port})"
        )
    items = ", ".join(f"{a}:{port}" + (f" ({n})" if n else "") for a, n in pairs)
    return f"reachable from containers or other machines at: {items}"
