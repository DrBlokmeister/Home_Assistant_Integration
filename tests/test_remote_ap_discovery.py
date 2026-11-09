from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path("custom_components/open_epaper_link/hub_discovery.py")
spec = importlib.util.spec_from_file_location("hub_discovery", MODULE_PATH)
assert spec and spec.loader
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


def test_only_firmware_apitem_shape_is_discoverable() -> None:
    item = {"ip": "192.168.88.42", "alias": "Peer", "count": 4, "channel": 11, "version": "001F"}
    assert discovery.discovery_candidate(item) == "192.168.88.42"
    assert discovery.discovery_candidate({"mesh": {"hub_ip": "192.168.88.42"}}) is None
    assert discovery.discovery_candidate({**item, "ip": "169.254.169.254"}) is None


def test_discovery_scope_is_local_private_and_unicast() -> None:
    assert discovery.same_discovery_network("192.168.88.10", "192.168.88.42")
    assert not discovery.same_discovery_network("192.168.88.10", "192.168.89.42")
    assert not discovery.same_discovery_network("192.168.88.10", "192.168.88.255")
    assert not discovery.same_discovery_network("192.0.2.10", "192.0.2.42")


def test_local_report_is_bound_to_reporting_ap() -> None:
    assert discovery.resolve_connected_ap("0.0.0.0", "192.0.2.74", False) == (
        "192.0.2.74", "local"
    )


def test_external_report_requires_valid_radio_address() -> None:
    assert discovery.resolve_connected_ap("192.0.2.74", "192.0.2.73", True) == (
        "192.0.2.74", "tag.apip"
    )
    assert discovery.resolve_connected_ap("0.0.0.0", "192.0.2.73", True) == (None, None)
    assert discovery.resolve_connected_ap("not-an-address", "192.0.2.73", True) == (None, None)
