"""Regression checks for persisted secondary AP devices."""

from unittest.mock import Mock

from custom_components.open_epaper_link.coordinator import Hub
from custom_components.open_epaper_link.entity import OpenEPaperLinkDiscoveredAPEntity

PEER = "192.0.2.74"


def test_discovered_ap_device_info_omits_configuration_url() -> None:
    hub = Mock()
    hub.get_discovered_hub.return_value = {"metadata": {"model": "Mini AP"}, "ip": PEER}
    entity = OpenEPaperLinkDiscoveredAPEntity(hub, PEER)

    device_info = entity.device_info
    assert "configuration_url" not in device_info
    assert device_info["via_device"] == ("open_epaper_link", "ap")
    assert device_info["identifiers"] == {("open_epaper_link", f"ap_{PEER}")}


def test_secondary_reconnect_refreshes_existing_metadata(monkeypatch) -> None:
    hub = Hub.__new__(Hub)
    hub.hass = Mock()
    hub._discovered_hubs = {PEER: {"last_seen": "2026-01-01T00:00:00+00:00"}}
    hub._schedule_storage_save = Mock()
    monkeypatch.setattr("custom_components.open_epaper_link.coordinator.async_dispatcher_send", Mock())

    hub._handle_connection(PEER, True)

    assert hub._discovered_hubs[PEER]["last_seen"] != "2026-01-01T00:00:00+00:00"
    hub._schedule_storage_save.assert_called_once()
