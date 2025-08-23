from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_EXTERNAL_HUB_DISCOVERED
from .hub import Hub

import logging

_LOGGER = logging.getLogger(__name__)


class OpenEPaperLinkWSBinarySensor(BinarySensorEntity):
    """Binary sensor indicating WebSocket connection state for a hub."""

    _attr_has_entity_name = True
    _attr_translation_key = "ws_connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: Hub, host: str) -> None:
        self._hub = hub
        self._host = host
        self._attr_unique_id = f"{hub.entry.entry_id}_{host}_ws_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"ap_{host}")},
            name=f"OpenEPaperLink AP {host}",
            model=self._hub.ap_model,
            manufacturer="OpenEPaperLink",
        )

    @property
    def is_on(self) -> bool:
        """Return true if the hub has an active WebSocket connection."""
        return self._hub.is_online(self._host)

    async def async_added_to_hass(self) -> None:
        """Register dispatcher to update state on connection changes."""
        signal = (
            f"{DOMAIN}_connection_status"
            if self._host == self._hub.host
            else f"{DOMAIN}_connection_status_{self._host}"
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal, self._handle_update)
        )

    @callback
    def _handle_update(self, _: bool) -> None:
        """Handle connection status updates."""
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up WebSocket connection binary sensors for OpenEPaperLink hubs."""
    hub = hass.data[DOMAIN][entry.entry_id]

    sensors = [OpenEPaperLinkWSBinarySensor(hub, hub.host)]

    # Include sensors for hubs discovered during initial setup
    for host in hub.discovered_hubs:
        sensors.append(OpenEPaperLinkWSBinarySensor(hub, host))

    async_add_entities(sensors)

    @callback
    def async_add_external_hub(host: str) -> None:
        async_add_entities([OpenEPaperLinkWSBinarySensor(hub, host)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_EXTERNAL_HUB_DISCOVERED,
            async_add_external_hub,
        )
    )
