"""Housekeeping 4: `openfactory run` names the addresses a non-loopback bind is reachable at."""

from __future__ import annotations

import ipaddress

import pytest

from openfactory.core import netinfo

FAKE = [("172.18.206.163", "eth0"), ("192.168.1.20", "wlan0")]


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_loopback_bind_prints_nothing(bind):
    assert netinfo.reachable_addresses(bind) == []
    assert netinfo.describe_reachable(bind, 5020) == ""


def test_wildcard_lists_every_address():
    line = netinfo.describe_reachable("0.0.0.0", 5020, FAKE)
    assert line == (
        "reachable from containers or other machines at: "
        "172.18.206.163:5020 (eth0), 192.168.1.20:5020 (wlan0)"
    )


def test_specific_address_is_the_only_one_reachable():
    assert netinfo.reachable_addresses("192.168.1.20") == [("192.168.1.20", "")]


def test_host_addresses_are_real_non_loopback_ipv4():
    pairs = netinfo.host_ipv4_addresses()
    print(pairs)
    for address, _ in pairs:
        ip = ipaddress.ip_address(address)
        assert ip.version == 4 and not ip.is_loopback


def test_no_address_found_still_says_so():
    line = netinfo.describe_reachable("0.0.0.0", 5020, [])
    assert "no IPv4 address found" in line
