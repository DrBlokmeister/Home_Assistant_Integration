from __future__ import annotations

PARALLEL_UPDATES = 0

import asyncio
import logging
from datetime import datetime, timezone
from typing import Final

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .util import is_ble_entry
from .entity import OpenEPaperLinkTagEntity, OpenEPaperLinkBLEEntity
from .runtime_data import OpenEPaperLinkConfigEntry
from .const import DOMAIN, SIGNAL_TAG_IMAGE_UPDATE
from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .image_decompressor import to_image
from .tag_types import TagType, get_tag_types_manager

_LOGGER: Final = logging.getLogger(__name__)

async def async_setup_entry(
        hass: HomeAssistant,
        entry: OpenEPaperLinkConfigEntry,
        async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the OpenEPaperLink image platform."""

    entry_data = entry.runtime_data

    if is_ble_entry(entry_data):
        mac_address = entry_data.mac_address
        name = entry_data.name
        device_metadata = entry_data.device_metadata

        image_entity = OpenEPaperLinkBLEImage(
            hass=hass,
            mac_address=mac_address,
            name=name,
            device_metadata=device_metadata,
            entry=entry,
        )
        async_add_entities([image_entity], True)
        return True

    hub = entry.runtime_data

    # Track added image entities to prevent duplicates
    added_image_entities = set()

    async def async_add_image_entity(tag_mac: str) -> None:

        # Skip if image entity already exists
        if tag_mac in added_image_entities:
            return

        # Skip if tag is blacklisted
        if tag_mac in hub.get_blacklisted_tags():
            _LOGGER.debug("Skipping image entity creation for blacklisted tag: %s", tag_mac)
            return

        # Skip AP (it's not a tag)
        if tag_mac == "ap":
            return

        image_entity = ESLImage(hass, tag_mac, hub)
        added_image_entities.add(tag_mac)
        async_add_entities([image_entity], True)

    # Add image entity for existing tags
    for tag_mac in hub.tags:
        await async_add_image_entity(tag_mac)

    # Register callback for new tag discovery
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_tag_discovered",
            async_add_image_entity
        )
    )

    # Register callback for blacklist updates
    async def handle_blacklist_update() -> None:
        """Handle updates to the tag blacklist.

    Processes changes to the blacklisted tags configuration by
    removing image entities for tags that have been blacklisted.

        When a tag is added to the blacklist:

        1. Its entry is removed from the 'added_image_entities' set
        2. Its corresponding image entity is removed from Home Assistant
        3. The image entity will automatically be excluded from future discoveries

        This ensures blacklisted tags don't appear in the UI and
        don't consume resources with unnecessary image processing.
        """
        for tag_mac in hub.get_blacklisted_tags():
            if tag_mac in added_image_entities:
                added_image_entities.remove(tag_mac)

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{DOMAIN}_blacklist_update",
            handle_blacklist_update
        )
    )

    return True

class ESLImage(OpenEPaperLinkTagEntity, ImageEntity):
    """Image entity class for OpenEPaperLink tags.

    Provides an image entity that shows the current content displayed
    on a tag by fetching its raw image data from the AP and
    converting it to a standard image format.
    """

    _attr_entity_registry_enabled_default = True
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, tag_mac: str, hub) -> None:
        """Initialize the image entity."""
        ImageEntity.__init__(self, hass)
        OpenEPaperLinkTagEntity.__init__(self, hub, tag_mac)
        self._attr_translation_key = "content"
        self._attr_unique_id = f"{tag_mac}_display_content"
        tag_data = hub.get_tag_data(tag_mac)
        self._name = f"{tag_data.get('tag_name', tag_mac)}"
        self._attr_content_type = "image/jpeg"
        self._cached_image: bytes | None = None
        self._last_updated: datetime | None = None
        self._tag_type = None
        self._image_hosts: tuple[str, ...] = ()
        self._refresh_pending = True
        self._image_generation = 0
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
                super().available
                and self._tag_mac in self._hub.tags
        )

    @property
    def image_last_updated(self) -> datetime | None:
        """Return the last updated image timestamp."""
        return self._last_updated

    async def _fetch_raw_image(self, hosts: tuple[str, ...]) -> bytes | None:
        """Try the trusted preview sources in routing order."""
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(10):
                for host in hosts:
                    try:
                        async with session.get(
                            f"http://{host}/current/{self._tag_mac}.raw",
                            timeout=aiohttp.ClientTimeout(total=4),
                            allow_redirects=False,
                        ) as response:
                            if response.status == 200:
                                if raw := await response.read():
                                    return raw
                    except (aiohttp.ClientError, TimeoutError) as err:
                        _LOGGER.debug("Preview source unavailable for %s: %s", self._tag_mac, err)
        except TimeoutError:
            _LOGGER.debug("Timed out fetching preview for %s", self._tag_mac)
        return None

    async def _get_tag_def(self) -> TagType | None:
        """Get tag definition for image decoding."""
        if self._tag_type is None:
            try:
                tag_data = self._hub.get_tag_data(self._tag_mac)
                hw_type = tag_data.get("hw_type")
                if hw_type is None:
                    return None
                tag_manager = await get_tag_types_manager(self.hass)
                tag_type = await tag_manager.get_tag_info(hw_type)
                if tag_type is None:
                    return None
                self._tag_type = tag_type
            except Exception as err:
                _LOGGER.error("Error getting tag definition for %s: %s", self._tag_mac, str(err))
                return None
        return self._tag_type

    async def async_image(self) -> bytes | None:
        """Return the last successful preview, refreshing if needed."""
        await self._refresh_image()
        return self._cached_image

    async def _refresh_image(self) -> None:
        """Refresh the cached image data."""
        async with self._refresh_lock:
            hosts = self._hub.image_hosts(self._tag_mac)
            if not hosts or (not self._refresh_pending and hosts == self._image_hosts):
                return
            self._image_hosts = hosts
            generation = self._image_generation
            raw_data = await self._fetch_raw_image(hosts)
            if not raw_data:
                return
            tag_def = await self._get_tag_def()
            if tag_def is None:
                return
            try:
                jpeg_data = await self.hass.async_add_executor_job(to_image, raw_data, tag_def)
            except Exception as err:
                _LOGGER.error("Error decoding image for %s: %s", self._tag_mac, err)
                return
            if generation == self._image_generation and hosts == self._hub.image_hosts(self._tag_mac):
                self._store_image(jpeg_data)
                self._refresh_pending = False

    async def async_added_to_hass(self) -> None:
        """Register callback when entity is added."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_TAG_IMAGE_UPDATE}_{self._tag_mac}",
                self._handle_tag_image_update
            )
        )
        self.async_on_remove(self._cancel_refresh)

    @callback
    def _handle_update(self) -> None:
        """Refresh when tag routing changes."""
        super()._handle_update()
        self._refresh_if_hosts_changed()

    @callback
    def _handle_connection_status(self, is_online: bool) -> None:
        """Refresh when AP connectivity changes the preview sources."""
        super()._handle_connection_status(is_online)
        self._refresh_if_hosts_changed()

    @callback
    def _refresh_if_hosts_changed(self) -> None:
        hosts = self._hub.image_hosts(self._tag_mac)
        if hosts != self._image_hosts:
            self._image_hosts = hosts
            self._handle_tag_image_update(True)

    @callback
    def _cancel_refresh(self) -> None:
        self._image_generation += 1
        if self._refresh_task is not None:
            self._refresh_task.cancel()

    @callback
    def _store_image(self, data: bytes) -> None:
        if data != self._cached_image:
            self._cached_image = data
            self._last_updated = datetime.now(timezone.utc)
            self.async_write_ha_state()

    @callback
    def _handle_tag_image_update(self, data) -> None:
        """Handle tag image updates."""
        if not data:
            return
        self._cancel_refresh()
        if isinstance(data, bytes):
            self._image_hosts = self._hub.image_hosts(self._tag_mac)
            self._refresh_pending = False
            self._store_image(data)
        else:
            self._refresh_pending = True
            self._refresh_task = self.hass.async_create_task(self._refresh_image())


class OpenEPaperLinkBLEImage(OpenEPaperLinkBLEEntity, ImageEntity):
    """Image entity for BLE OpenEPaperLink devices.

    Captures and displays the content generated by drawcustom service calls.
    """

    _attr_entity_registry_enabled_default = True
    _attr_has_entity_name = True

    def __init__(
            self,
            hass: HomeAssistant,
            mac_address: str,
            name: str,
            device_metadata: dict,
            entry: OpenEPaperLinkConfigEntry,
    ):
        """Initialize the BLE image entity."""
        ImageEntity.__init__(self, hass)
        OpenEPaperLinkBLEEntity.__init__(self, mac_address, name, entry)
        self._device_metadata = device_metadata
        self._attr_translation_key = "content"
        self._attr_unique_id = f"oepl_ble_{mac_address}_display_content"
        self._attr_content_type = "image/jpeg"
        self._cached_image: bytes | None = None
        self._last_updated: datetime | None = None

    @property
    def image_last_updated(self) -> datetime | None:
        """Return the last updated image timestamp."""
        return self._last_updated

    async def async_image(self) -> bytes | None:
        """Return cached image bytes."""
        return self._cached_image

    async def async_added_to_hass(self) -> None:
        """Register callback when entity is added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_TAG_IMAGE_UPDATE}_{self._mac_address}",
                self._handle_image_update
            )
        )

    @callback
    def _handle_image_update(self, data) -> None:
        """Handle image data updates."""
        if isinstance(data, bytes):
            self._cached_image = data
            self._last_updated = datetime.now()
            self.async_write_ha_state()
