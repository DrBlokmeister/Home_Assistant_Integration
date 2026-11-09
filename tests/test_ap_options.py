"""Exercise secondary AP options through Home Assistant's config flow manager."""

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry


pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")
DOMAIN = "open_epaper_link"
TAG = "001122AABBCCDDEE"


async def test_existing_entry_accepts_secondary_aps_without_runtime_data(hass):
    """An offline AP entry can be configured without replacing stored options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=4,
        data={"host": "192.0.2.10"},
        options={"blacklisted_tags": [TAG], "button_debounce": 2.0, "extra": True},
    )
    entry.add_to_hass(hass)
    manager = hass.config_entries.options

    form = await manager.async_init(entry.entry_id)
    assert form["type"] is FlowResultType.FORM
    defaults = form["data_schema"]({})
    assert defaults["secondary_aps"] == defaults["content_aps"] == ""
    assert defaults["discover_aps"] is True
    assert defaults["blacklisted_tags"] == [TAG]

    result = await manager.async_configure(
        form["flow_id"],
        {
            "secondary_aps": " 192.0.2.11 \n192.0.2.10\n192.0.2.11\n\n198.51.100.12",
            "discover_aps": False,
            "content_aps": f" {TAG.lower()} = 192.0.2.11 ",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data == {"host": "192.0.2.10"}
    assert entry.version == 4
    assert entry.options["secondary_aps"] == ["192.0.2.11", "198.51.100.12"]
    assert entry.options["discover_aps"] is False
    assert entry.options["content_aps"] == {TAG: "192.0.2.11"}
    assert entry.options["blacklisted_tags"] == [TAG]
    assert entry.options["button_debounce"] == 2.0
    assert entry.options["extra"] is True

    form = await manager.async_init(entry.entry_id)
    defaults = form["data_schema"]({})
    assert defaults["secondary_aps"] == "192.0.2.11\n198.51.100.12"
    assert defaults["content_aps"] == f"{TAG}=192.0.2.11"
    assert defaults["discover_aps"] is False

    result = await manager.async_configure(
        form["flow_id"], {"secondary_aps": "", "content_aps": ""}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["secondary_aps"] == []
    assert entry.options["content_aps"] == {}


@pytest.mark.parametrize(
    ("secondary_aps", "content_aps", "field"),
    [
        ("http://192.0.2.11", "", "secondary_aps"),
        ("127.0.0.1", "", "secondary_aps"),
        ("224.0.0.1", "", "secondary_aps"),
        ("192.0.2.11", f"{TAG}=192.0.2.99", "content_aps"),
        ("192.0.2.11", "invalid_mac=192.0.2.11", "content_aps"),
        ("192.0.2.11", f"{TAG}=192.0.2.11\n{TAG.lower()}=192.0.2.11", "content_aps"),
    ],
)
async def test_invalid_options_are_rejected(hass, secondary_aps, content_aps, field):
    """Reject unsafe addresses and ambiguous mappings without writing options."""
    entry = MockConfigEntry(domain=DOMAIN, version=4, data={"host": "192.0.2.10"})
    entry.add_to_hass(hass)
    manager = hass.config_entries.options

    form = await manager.async_init(entry.entry_id)
    result = await manager.async_configure(
        form["flow_id"], {"secondary_aps": secondary_aps, "content_aps": content_aps}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: f"invalid_{field}"}
    assert result["data_schema"]({})[field] == {
        "secondary_aps": secondary_aps,
        "content_aps": content_aps,
    }[field]
    assert entry.options == {}


async def test_ble_entry_still_has_no_options(hass):
    """Leave the existing direct BLE options behavior in place."""
    entry = MockConfigEntry(domain=DOMAIN, version=4, data={"device_type": "ble"})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_options_ble"
