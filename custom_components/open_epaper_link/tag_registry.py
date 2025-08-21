from __future__ import annotations

from typing import Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_TAG_UPDATE


class TagRegistry:
    """Global coordinator for OpenEPaperLink tag data."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the registry."""
        self.hass = hass
        self._data: Dict[str, dict] = {}

    @property
    def tags(self) -> list[str]:
        """Return list of tracked tag MAC addresses."""
        return list(self._data.keys())

    def get_tag_data(self, tag_mac: str) -> dict:
        """Return stored data for a tag."""
        return self._data.get(tag_mac, {})

    def update_tag(self, tag_mac: str, data: dict, source: str) -> None:
        """Update registry with new tag data and notify listeners."""
        data_copy = data.copy()
        data_copy["last_ap_host"] = source
        self._data[tag_mac] = data_copy
        async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")

    def remove_tag(self, tag_mac: str) -> None:
        """Remove a tag from the registry and notify listeners."""
        self._data.pop(tag_mac, None)
        async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")
