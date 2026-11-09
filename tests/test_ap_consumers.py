"""Regression tests for multi-AP preview and diagnostic consumers."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.open_epaper_link import diagnostics, image


PRIMARY = "192.0.2.10"
SECONDARY = "192.0.2.11"
TAG = "001122AABBCCDDEE"


async def test_preview_retains_cache_if_new_sources_have_no_image(monkeypatch):
    """An AP outage or missing preview should preserve the last good image."""
    hub = SimpleNamespace(
        get_tag_data=lambda _: {},
        image_hosts=Mock(return_value=(PRIMARY,)),
    )
    monkeypatch.setattr(image.ImageEntity, "__init__", lambda self, hass: None)
    entity = image.ESLImage(None, TAG, hub)
    monkeypatch.setattr(entity, "async_write_ha_state", Mock())
    fetch = AsyncMock(return_value=None)
    monkeypatch.setattr(entity, "_fetch_raw_image", fetch)
    entity._handle_tag_image_update(b"cached preview")

    hub.image_hosts.return_value = (SECONDARY, PRIMARY)

    assert await entity.async_image() == b"cached preview"
    fetch.assert_awaited_once_with((SECONDARY, PRIMARY))


async def test_in_flight_preview_does_not_replace_new_image(monkeypatch):
    """A completed older request must not overwrite a newer service image."""
    hub = SimpleNamespace(
        get_tag_data=lambda _: {},
        image_hosts=lambda _: (SECONDARY, PRIMARY),
    )
    monkeypatch.setattr(image.ImageEntity, "__init__", lambda self, hass: None)
    entity = image.ESLImage(None, TAG, hub)
    entity.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=b"old preview"))
    monkeypatch.setattr(entity, "async_write_ha_state", Mock())
    monkeypatch.setattr(entity, "_get_tag_def", AsyncMock(return_value=object()))
    monkeypatch.setattr(image, "to_image", lambda *_: b"old preview")
    fetching = asyncio.Event()
    finish = asyncio.Event()

    async def fetch(_):
        fetching.set()
        await finish.wait()
        return b"old raw data"

    monkeypatch.setattr(entity, "_fetch_raw_image", fetch)
    async with asyncio.timeout(2):
        async with asyncio.TaskGroup() as tasks:
            pending = tasks.create_task(entity.async_image())
            await fetching.wait()
            entity._handle_tag_image_update(b"new service image")
            finish.set()

    assert pending.result() == b"new service image"


async def test_diagnostics_redact_address_keys_and_content_destinations():
    """Keep AP status visible without disclosing the new routing configuration."""
    hub = Mock(spec=diagnostics.Hub)
    hub.online = False
    hub.ap_model = "ESP32"
    hub.ap_env = "fixture"
    hub.ap_status = {"ip": PRIMARY}
    hub.ap_config = {}
    hub.tags = [TAG]
    hub.get_blacklisted_tags.return_value = []
    hub.get_tag_data.return_value = {
        "tag_name": TAG,
        "connected_ap": SECONDARY,
        "content_ap": PRIMARY,
        "content_reporter": SECONDARY,
    }
    hub.get_discovered_hubs.return_value = {
        SECONDARY: {"online": True, "metadata": {"value": SECONDARY}},
    }
    entry = SimpleNamespace(
        title=f"OpenEPaperLink ({PRIMARY})",
        data={"host": PRIMARY},
        options={"secondary_aps": [SECONDARY], "content_aps": {TAG: SECONDARY}},
        version=4,
        minor_version=1,
        runtime_data=hub,
    )

    result = await diagnostics.async_get_config_entry_diagnostics(None, entry)

    serialized = json.dumps(result)
    assert PRIMARY not in serialized
    assert SECONDARY not in serialized
    assert TAG not in serialized
    assert result["discovered_hubs"][0]["online"] is True
