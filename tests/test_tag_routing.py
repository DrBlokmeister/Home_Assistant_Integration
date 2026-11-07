"""Operation-specific routing and non-destructive tag reconciliation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.exceptions import HomeAssistantError

from custom_components.open_epaper_link.coordinator import Hub

PRIMARY = "192.0.2.73"
SECONDARY = "192.0.2.74"
MAC = "0000AABBCCDDEE01"


@pytest.fixture
def hub(monkeypatch) -> Hub:
    instance = Hub.__new__(Hub)
    instance.host = PRIMARY
    instance.entry = SimpleNamespace(options={"secondary_aps": [SECONDARY]})
    instance.hass = Mock()
    instance._main_hub_ip = PRIMARY
    instance._connections = SimpleNamespace(
        online=lambda host: host == SECONDARY, hosts={PRIMARY, SECONDARY}
    )
    instance._data = {}
    instance._known_tags = set()
    instance._tag_keys = {}
    instance._blacklisted_tags = []
    instance._tag_manager = SimpleNamespace(get_hw_dimensions=lambda _: (128, 64))
    instance._schedule_storage_save = Mock()
    instance._update_device_name = Mock()
    instance._ap_request = AsyncMock()
    monkeypatch.setattr("custom_components.open_epaper_link.coordinator.async_dispatcher_send", Mock())
    return instance


@pytest.mark.parametrize("action", ["imgupload", "tag_cmd:refresh", "tag_cmd:clear", "save_cfg"])
def test_content_write_defaults_to_primary(hub, action) -> None:
    hub._data[MAC] = {"connected_ap": SECONDARY}
    assert hub.resolve_tag_target_host(action=action, tag_mac=MAC) == (PRIMARY, False)


async def test_rf_and_content_commands_route_independently(hub) -> None:
    hub._data[MAC] = {"connected_ap": SECONDARY}

    assert hub.is_tag_ap_online(MAC)
    assert not hub.online
    await hub.send_tag_cmd(f"open_epaper_link.{MAC.lower()}", "scan")
    assert hub._ap_request.await_args.kwargs["target_host"] == SECONDARY

    hub.entry.options["content_aps"] = {MAC: SECONDARY}
    await hub.send_tag_cmd(f"open_epaper_link.{MAC.lower()}", "refresh")
    assert hub._ap_request.await_args.kwargs["target_host"] == SECONDARY


def test_unverified_radio_never_gets_direct_write(hub) -> None:
    hub._data[MAC] = {"connected_ap": "192.0.2.99"}
    assert hub.resolve_tag_target_host(action="setled", tag_mac=MAC) == (PRIMARY, True)
    assert hub.image_hosts(MAC) == (PRIMARY,)


def test_explicit_content_destination_required_for_remote_owner(hub) -> None:
    hub._data[MAC] = {"connected_ap": SECONDARY, "primary_content_remote": True}
    with pytest.raises(HomeAssistantError) as error:
        hub.resolve_tag_target_host(action="imgupload", tag_mac=MAC)
    assert error.value.translation_key == "content_ap_required"
    hub.entry.options["content_aps"] = {MAC: SECONDARY}
    assert hub.resolve_tag_target_host(action="imgupload", tag_mac=MAC) == (SECONDARY, False)


async def test_replica_and_missing_snapshot_preserve_identity(hub) -> None:
    legacy_mac = MAC.lower()
    hub._tag_keys[MAC] = legacy_mac
    hub._known_tags.add(legacy_mac)
    hub._data[legacy_mac] = {
        "last_seen": 100, "connected_ap": SECONDARY, "reporter": SECONDARY,
        "binding_source": "local", "tag_name": "Old", "checkin_count": 7,
    }

    await hub._process_tag_data(MAC, {
        "lastseen": 103, "isexternal": True, "apip": SECONDARY,
        "alias": "Renamed", "pending": 2, "contentMode": 24,
    }, reporter=PRIMARY)
    data = hub.get_tag_data(MAC)
    assert data["last_seen"] == 100
    assert data["connected_ap"] == SECONDARY
    assert data["tag_name"] == "Renamed"
    assert data["pending"] == 2
    assert data["checkin_count"] == 7

    await hub._process_tag_data(MAC, {
        "lastseen": 104, "isexternal": True, "apip": SECONDARY, "contentMode": 255,
    }, reporter=PRIMARY)
    hub._read_ap_json = AsyncMock(return_value={"tags": []})
    await hub.async_load_all_tags(PRIMARY)
    assert hub.tags == [legacy_mac]
    assert hub.get_tag_data(MAC) == data


async def test_direct_report_replaces_recent_replica_without_double_counting(hub) -> None:
    hub._data[MAC] = {
        "last_seen": 103, "connected_ap": SECONDARY, "reporter": PRIMARY,
        "binding_source": "tag.apip", "tag_name": "Tag", "checkin_count": 7,
    }
    hub._known_tags.add(MAC)
    await hub._process_tag_data(MAC, {
        "lastseen": 102, "isexternal": False, "apip": "0.0.0.0", "hwType": 0,
    }, reporter=SECONDARY)
    data = hub.get_tag_data(MAC)
    assert data["reporter"] == SECONDARY
    assert data["binding_source"] == "local"
    assert data["checkin_count"] == 7
