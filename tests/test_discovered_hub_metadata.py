from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import Mock

REPO_ROOT = Path(".").resolve()
PACKAGE_ROOT = REPO_ROOT / "custom_components" / "open_epaper_link"

if "custom_components" not in sys.modules:
    custom_components_pkg = types.ModuleType("custom_components")
    custom_components_pkg.__path__ = [str(REPO_ROOT / "custom_components")]
    sys.modules["custom_components"] = custom_components_pkg

if "custom_components.open_epaper_link" not in sys.modules:
    oepl_pkg = types.ModuleType("custom_components.open_epaper_link")
    oepl_pkg.__path__ = [str(PACKAGE_ROOT)]
    sys.modules["custom_components.open_epaper_link"] = oepl_pkg

if "homeassistant.components" not in sys.modules:
    ha_components = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = ha_components

if "homeassistant.components.bluetooth" not in sys.modules:
    ha_bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    ha_bluetooth.async_address_present = lambda *args, **kwargs: True
    sys.modules["homeassistant.components.bluetooth"] = ha_bluetooth

if "custom_components.open_epaper_link.ble" not in sys.modules:
    ble_module = types.ModuleType("custom_components.open_epaper_link.ble")

    class _BLEDeviceMetadata:
        pass

    ble_module.BLEDeviceMetadata = _BLEDeviceMetadata
    sys.modules["custom_components.open_epaper_link.ble"] = ble_module

entity_spec = importlib.util.spec_from_file_location(
    "custom_components.open_epaper_link.entity",
    PACKAGE_ROOT / "entity.py",
)
entity_module = importlib.util.module_from_spec(entity_spec)
assert entity_spec and entity_spec.loader
sys.modules["custom_components.open_epaper_link.entity"] = entity_module
entity_spec.loader.exec_module(entity_module)
OpenEPaperLinkDiscoveredAPEntity = entity_module.OpenEPaperLinkDiscoveredAPEntity

coordinator_spec = importlib.util.spec_from_file_location(
    "custom_components.open_epaper_link.coordinator",
    PACKAGE_ROOT / "coordinator.py",
)
coordinator_module = importlib.util.module_from_spec(coordinator_spec)
assert coordinator_spec and coordinator_spec.loader
sys.modules["custom_components.open_epaper_link.coordinator"] = coordinator_module
coordinator_spec.loader.exec_module(coordinator_module)
Hub = coordinator_module.Hub
coordinator_module.async_dispatcher_send = Mock()


def test_discovered_ap_device_info_omits_configuration_url() -> None:
    hub = Mock()
    hub.get_discovered_hub.return_value = {
        "metadata": {"model": "Mini AP"},
        "ip": "10.0.20.74",
    }
    entity = OpenEPaperLinkDiscoveredAPEntity(hub, "10.0.20.74")

    device_info = entity.device_info

    assert "configuration_url" not in device_info
    assert device_info["via_device"] == ("open_epaper_link", "ap")


def test_upsert_discovered_hub_schedules_storage_save_for_new_hub() -> None:
    hub = Hub.__new__(Hub)
    hub._discovered_hubs = {}
    hub._schedule_storage_save = Mock()
    hub.hass = Mock()

    hub._upsert_discovered_hub(
        {
            "hub_id": "10.0.20.74",
            "ip": "10.0.20.74",
            "metadata": {"source": "tag_apip"},
            "evidence_path": "tag[AA11].apip",
        },
        source="tag.apip",
    )

    hub._schedule_storage_save.assert_called_once()


def test_upsert_discovered_hub_schedules_storage_save_for_metadata_update() -> None:
    hub = Hub.__new__(Hub)
    hub._discovered_hubs = {
        "10.0.20.74": {
            "hub_id": "10.0.20.74",
            "ip": "10.0.20.74",
            "last_seen": "2026-04-05T00:00:00+00:00",
            "discovery_sources": ["tag.apip"],
            "metadata": {"source": "tag_apip"},
            "evidence_path": "tag[AA11].apip",
        }
    }
    hub._schedule_storage_save = Mock()
    hub.hass = Mock()

    hub._upsert_discovered_hub(
        {
            "hub_id": "10.0.20.74",
            "ip": "10.0.20.74",
            "metadata": {"source": "http_sysinfo"},
            "evidence_path": "sys.info",
        },
        source="http_sysinfo",
    )

    hub._schedule_storage_save.assert_called_once()
