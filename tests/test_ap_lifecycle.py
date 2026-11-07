"""Exercise the fork's original storage and registry contracts through Core."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.open_epaper_link.ap_connections import APConnections
from custom_components.open_epaper_link.const import DOMAIN
from custom_components.open_epaper_link.coordinator import Hub, STORAGE_KEY
from custom_components.open_epaper_link.image import ESLImage

PRIMARY = "192.0.2.73"
SECONDARY = "192.0.2.74"
MAC = "0000AABBCCDDEE01"
pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")


async def test_existing_entry_keeps_registry_and_store_through_secondary_only_reload(
    hass, monkeypatch
) -> None:
    seen = int(time.time()) - 1
    store = Store(hass, 1, STORAGE_KEY)
    await store.async_save({
        "tags": {MAC: {
            "tag_mac": MAC, "tag_name": "Fixture tag", "last_seen": seen,
            "temperature": 19, "hw_type": 0, "version": "01",
        }},
        "discovered_hubs": {},
    })
    entry = MockConfigEntry(
        domain=DOMAIN, data={"host": PRIMARY},
        options={"secondary_aps": [SECONDARY]}, version=4,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old_entity = registry.async_get_or_create(
        "sensor", DOMAIN, f"{MAC}_temperature", config_entry=entry,
        suggested_object_id="existing_oepl_temperature",
    )

    async def read_ap(_hub, host, path, **_kwargs):
        if host == PRIMARY:
            raise TimeoutError
        if path == "sysinfo":
            return {"env": "fixture", "buildversion": "2.91", "ap_version": 31}
        if path == "get_db":
            return {"tags": []}
        return {"alias": "Fixture"}

    connected = asyncio.Event()
    closed = []

    async def session(manager, host):
        if host != SECONDARY:
            await asyncio.Event().wait()
            return
        manager._set_online(host, True)
        try:
            await manager._on_ready(host)
            await manager._on_message(host, (
                '{"tags":[{"mac":"' + MAC + '","lastseen":' + str(seen) + ','
                '"isexternal":false,"apip":"0.0.0.0","hwType":0,"temperature":19}]}'
            ))
            connected.set()
            await asyncio.Event().wait()
        finally:
            manager._set_online(host, False)
            closed.append(manager)

    monkeypatch.setattr(Hub, "_read_ap_json", read_ap)
    monkeypatch.setattr(APConnections, "_run", session)
    monkeypatch.setattr(ESLImage, "_fetch_raw_image", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "custom_components.open_epaper_link.coordinator.get_tag_types_manager",
        AsyncMock(return_value=SimpleNamespace(get_hw_dimensions=lambda _: (128, 64))),
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    async with asyncio.timeout(5):
        await connected.wait()
    original_hub = entry.runtime_data
    assert entry.state is ConfigEntryState.LOADED
    assert not original_hub.online
    assert original_hub.is_tag_ap_online(MAC)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_temperature") == old_entity.entity_id

    connected.clear()
    assert await hass.config_entries.async_reload(entry.entry_id)
    async with asyncio.timeout(5):
        await connected.wait()
    assert original_hub._connections in closed
    assert not original_hub._connections.hosts
    assert entry.runtime_data.is_tag_ap_online(MAC)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_temperature") == old_entity.entity_id

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(closed) == 2
    assert (await store.async_load())["tags"][MAC]["connected_ap"] == SECONDARY


async def test_legacy_opendisplay_entry_survives_setup(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"device_type": "ble", "protocol_type": "oepl"},
        version=4,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert hass.config_entries.async_get_entry(entry.entry_id) is entry
