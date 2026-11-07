import asyncio
import inspect
import socket
from urllib.parse import urlsplit
from datetime import datetime, timedelta, timezone
from typing import Final, Dict, Any, Callable, Awaitable

import json
import requests
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, CALLBACK_TYPE, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr
import logging

from .ap_connections import APConnections
from .const import (
    CONF_CONTENT_APS,
    CONF_DISCOVER_APS,
    CONF_SECONDARY_APS,
    DOMAIN,
    SIGNAL_AP_UPDATE,
    SIGNAL_REMOTE_AP_DISCOVERED,
    SIGNAL_TAG_IMAGE_UPDATE,
    SIGNAL_TAG_UPDATE,
)
from .hub_discovery import (
    discovery_candidate,
    normalize_ip,
    normalize_mac,
    resolve_connected_ap,
    same_discovery_network,
    uses_radio,
)
from .tag_types import get_tag_types_manager, get_hw_string

_LOGGER: Final = logging.getLogger(__name__)


STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_tags"
SAVE_DELAY = 10
AP_HTTP_TIMEOUT = 5
MAX_DISCOVERED_APS = 16
REPLICA_CLOCK_SKEW = 5




class Hub:
    """Central communication manager for OpenEPaperLink integration.

    This class manages all interaction with the OpenEPaperLink Access Point (AP),
    including:

    - WebSocket connection for real-time updates
    - Tag data management and state tracking
    - AP configuration and status monitoring
    - Event handling for tag interactions (buttons, NFC)
    - Persistent storage of tag data

    The Hub maintains the primary state for all tags and the AP itself,
    serving as the data source for all entities in the integration.

    It serves as a coordinator for all tag related updates,
     but it does not make sense converting it to a DataUpdateCoordinator,
     which would be pulling, not pushing.

    Attributes:
        hass: Home Assistant instance
        entry: Config entry containing connection details
        host: Hostname or IP of the OpenEPaperLink AP
        online: Boolean indicating if the AP is currently connected
        tags: List of known tag MAC addresses
        ap_config: Dictionary of current AP configuration settings
        ap_status: Dictionary of current AP status information
    """
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Handle WebSocket connection and process incoming messages.

        Manages the lifecycle of the WebSocket connection, including:

        - Establishing initial connection to the AP
        - Processing incoming messages (tag updates, AP status, etc.)
        - Handling connection errors and implementing reconnection logic
        - Broadcasting connection state changes to entities

        This is a long-running task that continues until shutdown is triggered.
        When connection errors occur, it implements a reconnection strategy
        with a fixed interval defined by RECONNECT_INTERVAL.

        Raises:
            No exceptions are raised as they are caught and logged internally.
        """
        self.hass = hass
        self.entry = entry
        self.host = entry.data["host"]
        self._main_hub_ip = normalize_ip(self.host)
        # aiohttp's ws_connect follows redirects unless they are intercepted.
        trace = aiohttp.TraceConfig()
        trace.on_request_redirect.append(self._reject_ap_redirect)
        self._session = async_create_clientsession(hass, trace_configs=[trace])
        self._shutdown_handler: CALLBACK_TYPE | None = None
        self._connections = APConnections(
            self._session,
            on_message=self._handle_message,
            on_connection=self._handle_connection,
            on_ready=self._initialize_ap,
            verify=self._verify_connection,
            create_task=lambda coro, name: entry.async_create_background_task(
                hass, coro, name, eager_start=False
            ),
        )
        self._closing = False
        self._running = False
        self._dirty = False
        self._save_scheduled = False
        self._store = Store[dict[str, any]](
            hass, STORAGE_VERSION, STORAGE_KEY, private=True, atomic_writes=True
        )
        self._data: dict[str, dict] = {}
        self._ap_data: dict[str, any] = {}
        self.ap_config: dict[str, any] = {}
        self._known_tags: set[str] = set()
        self._tag_keys: dict[str, str] = {}
        self.ap_env = None
        self.ap_model = "ESP32"

        self._tag_manager = None
        self._blacklisted_tags = entry.options.get("blacklisted_tags", [])
        self._last_button_press: Dict[str, datetime] = {}
        self._button_debounce_interval = timedelta(seconds=0.5)
        self._nfc_last_scan: Dict[str, datetime] = {}
        self._nfc_debounce_interval = timedelta(seconds=1)
        self._update_debounce_interval()
        self._ap_cmd_sem = asyncio.Semaphore(1)
        self._ap_cmd_cooldown = 0.5
        self._discovered_hubs: dict[str, dict[str, Any]] = {}
        self._secondary_status: dict[str, dict[str, Any]] = {}
        self._verified_peers: set[str] = set()
        self._probe_after: dict[str, float] = {}
        self._topology_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()

    def _update_debounce_interval(self) -> None:
        """Update event debounce intervals from integration options.

        Reads the button_debounce and nfc_debounce values from the
        integration's configuration options and updates the internal
        debounce interval time deltas accordingly.

        This prevents rapid duplicate events from buttons or NFC scans
        by setting minimum time intervals between consecutive events.
        """
        button_debounce_seconds = self.entry.options.get("button_debounce", 0.5)
        nfc_debounce_seconds = self.entry.options.get("nfc_debounce", 1.0)
        self._button_debounce_interval = timedelta(seconds=button_debounce_seconds)
        self._nfc_debounce_interval = timedelta(seconds=nfc_debounce_seconds)

    async def async_reload_config(self) -> None:
        """Apply options without recreating the entry or its devices."""
        await self.async_reload_blacklist()
        self._update_debounce_interval()
        if self._running:
            await self._sync_connections()

    @property
    def online(self) -> bool:
        """Return the primary AP's connection status."""
        return self._connections.online(self.host)

    def _connection_host(self, host: str) -> str:
        return self.host if host == self._main_hub_ip else host

    def is_ap_online(self, host: str | None) -> bool:
        """Report the current session state of the given AP."""
        return bool(host and self._connections.online(self._connection_host(host)))

    def is_tag_ap_online(self, tag_mac: str) -> bool:
        """Check the known radio session, with legacy relay for untracked APs."""
        radio = self.get_tag_data(tag_mac).get("connected_ap")
        if radio and self._connection_host(radio) in self._connections.hosts:
            return self.is_ap_online(radio)
        return self.online

    def _manual_peers(self) -> set[str]:
        return {
            host
            for value in self.entry.options.get(CONF_SECONDARY_APS, ())
            if (host := normalize_ip(value)) and host != self._main_hub_ip
        }

    def _discovery_enabled(self) -> bool:
        return self.entry.options.get(CONF_DISCOVER_APS, True)

    async def _resolve_primary_ip(self) -> None:
        if self._main_hub_ip:
            return
        host = urlsplit(f"http://{self.host}").hostname
        if not host:
            return
        try:
            async with asyncio.timeout(AP_HTTP_TIMEOUT):
                records = await self.hass.loop.getaddrinfo(host, None, family=socket.AF_INET)
        except (OSError, TimeoutError):
            return
        addresses = {record[4][0] for record in records}
        if len(addresses) == 1:
            self._main_hub_ip = normalize_ip(addresses.pop())

    async def async_setup_initial(self) -> None:
        """Restore the original stores before Home Assistant adds entities."""
        stored = await self._store.async_load()
        if stored:
            self._data = stored.get("tags", {})
            self._known_tags = set(self._data)
            self._tag_keys = {mac.upper(): mac for mac in self._data}
            self._discovered_hubs = stored.get("discovered_hubs", {})

        self._tag_manager = await get_tag_types_manager(self.hass)
        await self._resolve_primary_ip()
        manual_peers = self._manual_peers()
        self._verified_peers = self._stored_peers(manual_peers)
        # Existing tags can load while APs are offline and reconnect in the background.
        if not self._data:
            results = await asyncio.gather(
                *(self._probe_ap(host) for host in (
                    self.host, *sorted(manual_peers | self._verified_peers)
                )),
            )
            if not any(results):
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="ap_cannot_connect",
                    translation_placeholders={"host": self.host, "error": "No configured AP responded"},
                )
        else:
            # The AP model must be known before the primary device is registered when reachable.
            await self._probe_ap(self.host)

    async def async_start_websocket(self) -> None:
        """Start one independently reconnecting task for each active AP."""
        if self._closing or self._running:
            return
        self._shutdown_handler = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_shutdown
        )
        self._running = True
        await self._sync_connections()

    async def _sync_connections(self) -> None:
        async with self._topology_lock:
            if self._closing:
                return
            manual = self._manual_peers()
            allowed = {self.host, *manual}
            self._verified_peers = self._stored_peers(manual)
            allowed.update(self._verified_peers)
            for host in self._connections.hosts - allowed:
                await self._connections.remove(host)
            if self._closing:
                return
            for host in sorted(allowed):
                if host in manual and host not in self._discovered_hubs:
                    self._upsert_discovered_hub({"hub_id": host, "ip": host}, "configured")
                self._connections.start(host)

    async def _handle_shutdown(self, _event) -> None:
        await self.shutdown()

    async def shutdown(self) -> None:
        """Stop all AP sessions and flush pending writes before a reload."""
        async with self._shutdown_lock:
            if self._closing:
                return
            self._closing = True
            if self._shutdown_handler is not None:
                self._shutdown_handler()
                self._shutdown_handler = None
            await self._connections.stop()
            if self._dirty:
                await self._store.async_save(self._build_storage_payload())

    def _schedule_storage_save(self) -> None:
        self._dirty = True
        if not self._save_scheduled:
            self._save_scheduled = True
            self._store.async_delay_save(self._delayed_storage_payload, SAVE_DELAY)

    def _delayed_storage_payload(self) -> dict[str, Any]:
        self._save_scheduled = False
        return self._build_storage_payload()

    async def _read_ap_json(self, host: str, path: str, **kwargs) -> dict:
        async with asyncio.timeout(AP_HTTP_TIMEOUT):
            async with self._session.get(
                f"http://{host}/{path}", allow_redirects=False, **kwargs
            ) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status} for /{path}")
                data = await response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Invalid JSON object from /{path}")
        return data

    @staticmethod
    async def _reject_ap_redirect(_session, _context, _params) -> None:
        raise aiohttp.ClientError("OpenEPaperLink AP redirects are unsupported")

    async def _probe_ap(self, host: str) -> bool:
        try:
            data = await self._read_ap_json(host, "sysinfo")
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False
        env = data.get("env")
        if not isinstance(env, str) or not env:
            return False
        if host != self.host and (
            not isinstance(data.get("buildversion"), str)
            or type(data.get("ap_version")) is not int
        ):
            return False
        if host == self.host:
            self.ap_env = env
            self.ap_model = self._format_ap_model(env)
        return True

    def _stored_peers(self, manual: set[str]) -> set[str]:
        """Keep retrying previously verified peers in a trusted discovery network."""
        if not self._discovery_enabled():
            return set()
        sources = {self._main_hub_ip, *manual}
        peers = [
            host for host, data in self._discovered_hubs.items()
            if host not in manual and host != self._main_hub_ip
            and data.get("verified") is True
            and any(same_discovery_network(source, host) for source in sources)
        ][:MAX_DISCOVERED_APS]
        return set(peers)

    async def _verify_connection(self, host: str) -> bool:
        """Revalidate secondary APs before opening each session."""
        return host == self.host or await self._probe_ap(host)

    async def _discover_peer(self, reporter: str, item: dict) -> None:
        host = discovery_candidate(item)
        if (
            not self._discovery_enabled() or not host or host in self._connections.hosts
            or host == self._main_hub_ip
            or len(self._verified_peers) >= MAX_DISCOVERED_APS
            or not same_discovery_network(
                self._main_hub_ip if reporter == self.host else normalize_ip(reporter), host
            )
        ):
            return
        now = self.hass.loop.time()
        self._probe_after = {ip: until for ip, until in self._probe_after.items() if until > now}
        if host in self._probe_after or len(self._probe_after) >= MAX_DISCOVERED_APS:
            return
        self._probe_after[host] = now + 60
        if not await self._probe_ap(host) or self._closing or not self._discovery_enabled():
            return
        if not same_discovery_network(self._main_hub_ip, host) and not any(
            same_discovery_network(manual_host, host) for manual_host in self._manual_peers()
        ):
            return
        self._verified_peers.add(host)
        self._upsert_discovered_hub(
            {"hub_id": host, "ip": host, "metadata": {
                key: item[key] for key in ("alias", "channel", "version")
            }},
            "apitem",
        )
        self._discovered_hubs[host]["verified"] = True
        self._schedule_storage_save()
        self._connections.start(host)

    def _handle_connection(self, host: str, online: bool) -> None:
        """Refresh entities after either AP connects or disconnects."""
        if online and host in self._discovered_hubs:
            self._discovered_hubs[host]["last_seen"] = datetime.now(timezone.utc).isoformat()
            self._schedule_storage_save()
        async_dispatcher_send(self.hass, f"{DOMAIN}_connection_status", online)
        async_dispatcher_send(self.hass, SIGNAL_AP_UPDATE)

    async def _initialize_ap(self, host: str) -> None:
        """Request discovery and refresh the reporting AP's tag inventory."""
        if host == self.host and not self.ap_env:
            await self._probe_ap(host)
        try:
            await self._read_ap_config(host)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Could not read AP configuration: %s", err)
        try:
            await self.async_load_all_tags(host)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Could not refresh AP tag inventory: %s", err)

    async def _handle_message(self, host: str, message: str) -> bool:
        """Process a WebSocket report with its AP provenance."""
        try:
            data = json.loads("{" + message.split("{", 1)[-1])
            if isinstance(system := data.get("sys"), dict):
                if host == self.host:
                    await self._handle_system_message(system)
                else:
                    self._secondary_status[host] = {
                        key: system[key] for key in (
                            "recordcount", "rssi", "uptime", "apstate", "runstate"
                        ) if key in system
                    }
                    async_dispatcher_send(self.hass, SIGNAL_AP_UPDATE)
            if isinstance(tags := data.get("tags"), list):
                for tag in tags:
                    if isinstance(tag, dict) and (mac := normalize_mac(tag.get("mac"))):
                        await self._process_tag_data(mac, tag, reporter=host)
            if isinstance(item := data.get("apitem"), dict):
                if item.get("type") == "change":
                    if host == self.host:
                        await self.async_update_ap_config()
                else:
                    await self._discover_peer(host, item)
            if isinstance(log := data.get("logMsg"), str):
                await self._handle_log_message(log, host)
            return data.get("errMsg") == "REBOOTING"
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Ignoring invalid AP message: %s", err)
            return False

    async def _handle_system_message(self, sys_data: dict) -> None:
        """Process a system message from the AP.

        Updates the AP status information based on system data, including:

        - IP address and Wi-Fi settings
        - Memory usage (heap, database size)
        - Tag counts and AP state
        - Runtime information

        This method is called when the AP sends a "sys" WebSocket message,
        which typically happens periodically or after state changes.

        Args:
            sys_data: Dictionary containing AP system status information
        """

        # Preserve existing values for fields that are not in every message
        current_low_batt = self._ap_data.get("low_battery_count", 0)
        current_timeout = self._ap_data.get("timeout_count", 0)

        self._ap_data = {
            "ip": self.host,
            "sys_time": sys_data.get("currtime"),
            "heap": sys_data.get("heap"),
            "record_count": sys_data.get("recordcount"),
            "db_size": sys_data.get("dbsize"),
            "little_fs_free": sys_data.get("littlefsfree"),
            "ps_ram_free": sys_data.get("psfree"),
            "rssi": sys_data.get("rssi"),
            "ap_state": self._get_ap_state_string(sys_data.get("apstate")),
            "run_state": self._get_ap_run_state_string(sys_data.get("runstate")),
            "temp": sys_data.get("temp"),
            "wifi_status": sys_data.get("wifistatus"),
            "wifi_ssid": sys_data.get("wifissid"),
            "uptime": sys_data.get("uptime"),
            "low_battery_count": sys_data.get("lowbattcount", current_low_batt),
            "timeout_count": sys_data.get("timeoutcount", current_timeout),
        }

        async_dispatcher_send(self.hass, SIGNAL_AP_UPDATE)

    async def _handle_log_message(self, log_msg: str, reporter: str) -> None:
        """Process a log message from the AP.

        Parses log messages for specific events that require action:
        - Block transfer requests: Updates the block_requests counter
        - Transfer completion: Triggers image update notification

        Args:
            log_msg: Raw log message string from the AP
        """
        if "block request" in log_msg:
            # Extract MAC address from block request message
            # Example: "0000000000123456 block request /current/0000000000123456_452783.pending block 0"
            parts = log_msg.split()
            if len(parts) > 0:
                tag_mac = self._tag_keys.get(parts[0].upper(), parts[0].upper())
                if tag_mac in self._data and reporter == self._data[tag_mac].get("reporter"):
                    block_requests = self._data[tag_mac].get("block_requests", 0) + 1
                    self._data[tag_mac]["block_requests"] = block_requests
                    self._schedule_storage_save()
                    # Notify of update
                    async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")
        if "reports xfer complete" in log_msg:
            # Extract MAC address from block request message
            parts = log_msg.split()
            if len(parts) > 0:
                tag_mac = self._tag_keys.get(parts[0].upper(), parts[0].upper())
                if tag_mac in self._data and reporter == self.image_hosts(tag_mac)[0]:
                    # Notify of update
                    async_dispatcher_send(self.hass, f"{SIGNAL_TAG_IMAGE_UPDATE}_{tag_mac}", True)

    async def _process_tag_data(
        self, tag_mac: str, tag_data: dict, is_initial_load: bool = False, *, reporter: str
    ) -> bool:
        """Process tag data and update internal state.

        Handles updates for a single tag, including:

        - Updating stored tag information
        - Calculating runtime and update counters
        - Managing tag discovery events
        - Broadcasting update events to entities
        - Triggering device events for buttons/NFC (with debouncing)

        Args:
            tag_mac: MAC address of the tag
            tag_data: Dictionary containing tag properties from the AP
            is_initial_load: True if this is part of initial loading at startup,
                             which affects event triggering behavior

        Returns:
            bool: True if this was a newly discovered tag, False for an update

        Raises:
            No exceptions are raised as they are caught and logged internally.
        """

        # A firmware deletion marker is not an authoritative, durable tombstone.
        if tag_mac in self._blacklisted_tags or tag_data.get("contentMode") == 255:
            return False

        tag_mac = self._tag_keys.get(tag_mac.upper(), tag_mac)
        is_new_tag = tag_mac not in self._known_tags
        existing_data = self._data.get(tag_mac, {})
        last_seen = tag_data.get("lastseen")
        max_seen = datetime.now(timezone.utc).timestamp() + 300
        if type(last_seen) is not int or not 0 <= last_seen <= max_seen:
            return False
        previous_seen = existing_data.get("last_seen") or 0
        if not isinstance(previous_seen, (int, float)) or not 0 <= previous_seen <= max_seen:
            previous_seen = 0
        is_external = tag_data.get("isexternal")
        connected_ap, binding_source = resolve_connected_ap(tag_data.get("apip"), reporter, is_external)
        previous_radio = existing_data.get("connected_ap")
        previous_reporter = existing_data.get("reporter")
        content_host = self._content_host(tag_mac)
        # UDP replicas can stamp the same check-in a few seconds later.
        replaces_replica = (
            binding_source == "local" and existing_data.get("binding_source") == "tag.apip"
            and previous_radio and self._connection_host(previous_radio) == reporter
            and 0 <= previous_seen - last_seen <= REPLICA_CLOCK_SKEW
        )
        if last_seen < previous_seen and not replaces_replica:
            if reporter in (content_host, self.host) and previous_reporter != reporter:
                self._update_content_metadata(
                    tag_mac, tag_data, existing_data, is_initial_load, reporter=reporter
                )
            return False
        replicated = binding_source == "tag.apip"
        secondary_copy = replicated and (
            self.is_ap_online(connected_ap)
            or (previous_radio != connected_ap and self.is_ap_online(previous_radio)
                and last_seen <= previous_seen + REPLICA_CLOCK_SKEW)
        )
        adopts_radio = binding_source == "local" and (
            previous_reporter is None
            or (connected_ap != previous_radio and not self.is_ap_online(previous_radio))
        )
        metadata_only = bool(existing_data) and not replaces_replica and (
            secondary_copy or (last_seen == previous_seen and not adopts_radio)
        )
        if metadata_only:
            if reporter in (content_host, previous_reporter, self.host):
                self._update_content_metadata(
                    tag_mac, tag_data, existing_data, is_initial_load,
                    include_checkin=not replicated, reporter=reporter,
                )
            return False

        # A replica can initialize state but does not repeat an online radio's physical events.
        new_checkin = (
            not is_initial_load and last_seen > previous_seen
            and not secondary_copy and connected_ap is not None
        )
        if "alias" in tag_data and (
            reporter == content_host or existing_data.get("content_reporter") != content_host
        ):
            tag_name = tag_data["alias"] or tag_mac
        else:
            tag_name = existing_data.get("tag_name") or tag_mac
        next_update = tag_data.get("nextupdate")
        next_checkin = tag_data.get("nextcheckin")
        lqi = tag_data.get("LQI")
        rssi = tag_data.get("RSSI")
        temperature = tag_data.get("temperature")
        battery_mv = tag_data.get("batteryMv")
        pending = tag_data.get("pending")
        hw_type = tag_data.get("hwType", existing_data.get("hw_type")) or 0
        hw_string = get_hw_string(hw_type)
        width, height = self._tag_manager.get_hw_dimensions(hw_type)
        content_mode = tag_data.get("contentMode")
        wakeup_reason = self._get_wakeup_reason_string(tag_data.get("wakeupReason"))
        capabilities = tag_data.get("capabilities")
        hashv = tag_data.get("hash")
        modecfgjson = tag_data.get("modecfgjson")
        previous_binding_source = existing_data.get("binding_source")
        binding_last_updated = existing_data.get("binding_last_updated")
        if connected_ap is None:
            connected_ap, binding_source = previous_radio, previous_binding_source
        elif (connected_ap, binding_source) != (previous_radio, previous_binding_source):
            binding_last_updated = datetime.now(timezone.utc).isoformat()
        rotate = tag_data.get("rotate")
        lut = tag_data.get("lut")
        channel = tag_data.get("ch")
        version = tag_data.get("ver")
        update_count = tag_data.get("updatecount")

        if existing_data and existing_data.get("tag_name") != tag_name:
            self._update_device_name(tag_mac, tag_name)

        # Calculate runtime delta (only if this is not the initial load)
        runtime_delta = 0
        runtime_total =  existing_data.get("runtime", 0)
        if new_checkin and existing_data:
            runtime_delta = self._calculate_runtime_delta(tag_data, existing_data)
            runtime_total += runtime_delta

        # Update boot count if this is a power-on event
        boot_count = existing_data.get("boot_count", 1)
        if new_checkin and tag_data.get("wakeupReason") in (1, 252, 254):  # BOOT, FIRSTBOOT, WDT_RESET
            boot_count += 1
            runtime_total = 0  # Reset runtime on boot

        # Update check-in counter
        checkin_count = existing_data.get("checkin_count", 0)
        if new_checkin:
            checkin_count += 1

        # Get existing block request count
        block_requests = existing_data.get("block_requests", 0)

        # Update tag data
        self._data[tag_mac] = {
            "tag_mac": tag_mac,
            "tag_name": tag_name,
            "last_seen": last_seen,
            "next_update": next_update,
            "next_checkin": next_checkin,
            "lqi": lqi,
            "rssi": rssi,
            "temperature": temperature,
            "battery_mv": battery_mv,
            "pending": pending,
            "hw_type": hw_type,
            "width": width,
            "height": height,
            "hw_string": hw_string,
            "content_mode": self._get_content_mode_string(content_mode),
            "wakeup_reason": wakeup_reason,
            "capabilities": capabilities,
            "hash": hashv,
            "modecfgjson": modecfgjson,
            "is_external": is_external,
            "connected_ap": connected_ap,
            "reporter": reporter,
            "binding_source": binding_source,
            "binding_last_updated": binding_last_updated,
            "rotate": rotate,
            "lut": lut,
            "channel": channel,
            "version": version,
            "update_count": update_count,
            "runtime": runtime_total,
            "boot_count": boot_count,
            "checkin_count": checkin_count,
            "block_requests": block_requests,
        }
        updated = self._data[tag_mac]
        for key in ("content_reporter", "content_report_at", "primary_content_remote", "primary_report_at"):
            if key in existing_data:
                updated[key] = existing_data[key]
        if reporter == self.host:
            self._update_primary_content_flag(updated, tag_data)
        if reporter == content_host:
            updated["content_reporter"] = reporter
            updated["content_report_at"] = last_seen
        elif existing_data.get("content_reporter") == content_host:
            for key in ("pending", "hash", "next_update", "modecfgjson", "content_mode", "update_count"):
                updated[key] = existing_data.get(key)

        # Handle new tag discovery
        if is_new_tag:
            self._tag_keys[tag_mac.upper()] = tag_mac
            self._known_tags.add(tag_mac)
            _LOGGER.debug("Discovered new tag: %s", tag_mac)
            # Fire discovery event before saving
            async_dispatcher_send(self.hass, f"{DOMAIN}_tag_discovered", tag_mac)

        self._schedule_storage_save()
        if (
            not is_initial_load and existing_data and updated["hash"]
            and updated["hash"] != existing_data.get("hash")
        ):
            async_dispatcher_send(self.hass, f"{SIGNAL_TAG_IMAGE_UPDATE}_{tag_mac}", True)

        # Fire state update event
        async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")

        # Handle wakeup event if needed and not initial load
        wakeup_reason = tag_data.get("wakeupReason")
        if new_checkin and wakeup_reason is not None:
            reason_string = self._get_wakeup_reason_string(wakeup_reason)

            should_fire = True
            current_time = datetime.now()

            # Apply debouncing based on event type
            if reason_string in ["BUTTON1", "BUTTON2", "BUTTON3", "BUTTON4", "BUTTON5", "BUTTON6", "BUTTON7", "BUTTON8", "BUTTON9", "BUTTON10"]:
                # Button debouncing
                debounce_key = f"{tag_mac}_{reason_string}"
                last_event = self._last_button_press.get(debounce_key)
                if last_event and (current_time - last_event) <= self._button_debounce_interval:
                    should_fire = False
                else:
                    self._last_button_press[debounce_key] = current_time

            elif reason_string == "NFC":
                # NFC debouncing
                debounce_key = f"{tag_mac}_NFC"
                last_event = self._nfc_last_scan.get(debounce_key)
                if last_event and (current_time - last_event) <= self._nfc_debounce_interval:
                    should_fire = False
                else:
                    self._nfc_last_scan[debounce_key] = current_time

            if should_fire:
                device_registry = dr.async_get(self.hass)
                device = device_registry.async_get_device(
                    identifiers={(DOMAIN, tag_mac)}
                )
                if device:
                    self.hass.bus.async_fire(f"{DOMAIN}_event", {
                        "device_id": device.id,
                        "type": reason_string
                    })

        return is_new_tag

    def _update_device_name(self, tag_mac: str, tag_name: str) -> None:
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, tag_mac)}):
            registry.async_update_device(device.id, name=tag_name)

    def _update_content_metadata(
        self, tag_mac: str, report: dict, existing: dict, is_initial_load: bool,
        *, reporter: str, include_checkin: bool = False,
    ) -> None:
        """Accept content updates independently of radio observation ordering."""
        if reporter == self.host:
            self._update_primary_content_flag(existing, report)
        if (
            existing.get("content_reporter") == reporter
            and report["lastseen"] < existing.get("content_report_at", 0)
        ):
            return
        content_host = self._content_host(tag_mac)
        updates = {}
        if reporter == content_host or existing.get("content_reporter") != content_host:
            fields = {
                "pending": "pending", "hash": "hash", "nextupdate": "next_update",
                "modecfgjson": "modecfgjson", "updatecount": "update_count",
            }
            updates.update({field: report[key] for key, field in fields.items() if key in report})
            if "alias" in report:
                updates["tag_name"] = report["alias"] or tag_mac
            if "contentMode" in report:
                updates["content_mode"] = self._get_content_mode_string(report["contentMode"])
        if include_checkin and "nextcheckin" in report:
            updates["next_checkin"] = report["nextcheckin"]
        if reporter == content_host:
            updates.update(content_reporter=reporter, content_report_at=report["lastseen"])
        if not any(existing.get(field) != value for field, value in updates.items()):
            return
        old_hash = existing.get("hash")
        if "tag_name" in updates and updates["tag_name"] != existing.get("tag_name"):
            self._update_device_name(tag_mac, updates["tag_name"])
        existing.update(updates)
        self._schedule_storage_save()
        async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")
        if not is_initial_load and updates.get("hash") and updates["hash"] != old_hash:
            async_dispatcher_send(self.hass, f"{SIGNAL_TAG_IMAGE_UPDATE}_{tag_mac}", True)

    def _update_primary_content_flag(self, data: dict, report: dict) -> None:
        mode = report.get("contentMode")
        previous_time = data.get("primary_report_at", 0)
        if (
            type(mode) is int and report["lastseen"] >= previous_time
            and (report["lastseen"], mode == 12) != (previous_time, data.get("primary_content_remote"))
        ):
            data["primary_content_remote"] = mode == 12
            data["primary_report_at"] = report["lastseen"]
            self._schedule_storage_save()

    async def _run_ap_command(self, func: Callable[[], Any | Awaitable[Any]]) -> Any:
        async with self._ap_cmd_sem:
            if self._ap_cmd_cooldown:
                await asyncio.sleep(self._ap_cmd_cooldown)
            # If it’s a coroutine function, await it; otherwise run in executor
            if inspect.iscoroutinefunction(func):
                return await func()
            return await self.hass.async_add_executor_job(func)

    def _content_host(self, tag_mac: str | None) -> str:
        host = self.entry.options.get(CONF_CONTENT_APS, {}).get(tag_mac.upper() if tag_mac else None)
        return host if host in self._manual_peers() else self.host

    def resolve_tag_target_host(
        self,
        *,
        action: str,
        entity_id: str | None = None,
        tag_mac: str | None = None,
    ) -> tuple[str, bool]:
        """Resolve the AP for a specific firmware operation."""
        if tag_mac is None and entity_id:
            tag_mac = entity_id.rpartition(".")[2].upper()
        if not uses_radio(action):
            target = self._content_host(tag_mac)
            data = self.get_tag_data(tag_mac) if tag_mac else {}
            legacy_remote = (
                data.get("reporter") in (None, self.host)
                and data.get("content_mode") == self._get_content_mode_string(12)
            )
            if target == self.host and data.get("primary_content_remote", legacy_remote):
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="content_ap_required",
                    translation_placeholders={"tag": tag_mac},
                )
            return target, False

        radio = self.get_tag_data(tag_mac).get("connected_ap") if tag_mac else None
        if radio and self.is_ap_online(radio):
            return self._connection_host(radio), False
        return self.host, True

    def image_hosts(self, tag_mac: str) -> tuple[str, ...]:
        """Prefer the content AP and fall back to verified read-only sources."""
        data = self.get_tag_data(tag_mac)
        hosts = [self._content_host(tag_mac)]
        for host in (data.get("reporter"), data.get("connected_ap")):
            if host and self.is_ap_online(host):
                hosts.append(self._connection_host(host))
        return tuple(dict.fromkeys((*hosts, self.host)))

    async def _ap_request(
        self,
        method: str,
        path: str,
        *,
        data=None,
        timeout=10,
        action: str,
        target_host: str | None = None,
    ) -> requests.Response:
        request_host = target_host or self.host
        url = f"http://{request_host}/{path.lstrip('/')}"
        def call():
            return requests.request(method, url, data=data, timeout=timeout, allow_redirects=False)
        try:
            resp = await self._run_ap_command(call)
        except requests.exceptions.Timeout:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ap_timeout_action",
                translation_placeholders={"action": action},
            ) from None
        except requests.exceptions.RequestException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ap_network_error_action",
                translation_placeholders={"action": action, "error": str(err)}
            ) from err
        if resp.status_code != 200:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="ap_failed_action_http",
                translation_placeholders={"action": action, "status_code": resp.status_code, "response_text": resp.text},
            )
        return resp

    async def set_led_pattern(self, entity_id: str, pattern: str) -> None:
        mac = entity_id.split(".")[1].upper()
        target_host, _ = self.resolve_tag_target_host(
            action="setled",
            entity_id=entity_id,
            tag_mac=mac,
        )
        await self._ap_request(
            "get",
            f"led_flash?mac={mac}&pattern={pattern}",
            action=f"update LED for {entity_id}",
            target_host=target_host,
        )
        _LOGGER.info("Updated LED pattern for %s via AP %s", entity_id, target_host)

    async def send_tag_cmd(self, entity_id: str, cmd: str) -> bool:
        mac = entity_id.split(".")[1].upper()
        target_host, _ = self.resolve_tag_target_host(
            action=f"tag_cmd:{cmd}",
            entity_id=entity_id,
            tag_mac=mac,
        )
        await self._ap_request(
            "post",
            "tag_cmd",
            data={"mac": mac, "cmd": cmd},
            action=f"send {cmd} to {entity_id}",
            target_host=target_host,
        )
        _LOGGER.info("Sent %s command to %s via AP %s", cmd, entity_id, target_host)
        return True

    async def reboot_ap(self) -> bool:
        await self._ap_request("post", "reboot", action="reboot OEPL AP")
        _LOGGER.info("Rebooted OEPL AP")
        return True

    async def set_ap_config_item(self, key: str, value: str | int) -> bool: # TODO listen to apitem for changes?
        if key in ("sleeptime1", "sleeptime2"):
            data = {"sleeptime1": self.ap_config.get("sleeptime1", 0),
                    "sleeptime2": self.ap_config.get("sleeptime2", 0), key: value}
        else:
            data = {key: value}

        await self._ap_request(
            "post",
            "save_apcfg",
            data=data,
            action=f"set AP config {key}",
        )
        for k, v in data.items():
            self.ap_config[k] = v
        _LOGGER.info("Set AP config %s = %s", key, value)
        return True

    async def async_load_all_tags(self, host: str) -> None:
        """Add or refresh records from a snapshot. An omission never prunes data."""
        position = 0
        seen_positions = {position}
        while True:
            data = await self._read_ap_json(host, "get_db", params={"pos": position})
            tags = data.get("tags", [])
            if not isinstance(tags, list):
                raise ValueError("Invalid tag inventory")
            for tag in tags:
                if isinstance(tag, dict) and (mac := normalize_mac(tag.get("mac"))):
                    await self._process_tag_data(mac, tag, is_initial_load=True, reporter=host)
            position = data.get("continu", 0)
            if type(position) is not int or position <= 0 or position in seen_positions:
                break
            seen_positions.add(position)

    async def async_reload_blacklist(self) -> None:
        """Reload the tag blacklist from config entry options.

        Updates the blacklist based on current integration options and:

        - Removes blacklisted tags from active tracking
        - Triggers entity and device removal for blacklisted tags
        - Updates persistent storage to reflect changes

        This is called when the integration options are updated.
        """
        entry = self.entry
        old_blacklist = self._blacklisted_tags.copy()
        self._blacklisted_tags = entry.options.get("blacklisted_tags", [])

        # Remove blacklisted tags from known tags and data
        for tag_mac in self._blacklisted_tags:
            if tag_mac in self._known_tags:
                self._known_tags.remove(tag_mac)
                self._data.pop(tag_mac, None)
                # Notify that this tag's state has changed
                async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")

        # If the blacklist has changed, trigger cleanup
        if set(old_blacklist) != set(self._blacklisted_tags):
            # Trigger entity and device removal
            async_dispatcher_send(self.hass, f"{DOMAIN}_blacklist_update")

            # Give Home Assistant time to process removals
            await asyncio.sleep(0.1)

            # Force a state refresh for all remaining tags
            for tag_mac in self._known_tags:
                if tag_mac not in self._blacklisted_tags:
                    async_dispatcher_send(self.hass, f"{SIGNAL_TAG_UPDATE}_{tag_mac}")

            # Save updated data to storage
            self._save_scheduled = False
            await self._store.async_save(self._build_storage_payload())

    async def _read_ap_config(self, host: str) -> None:
        """Request firmware discovery and update primary configuration entities."""
        config = await self._read_ap_json(host, "get_ap_config")
        if host == self.host and config != self.ap_config:
            self.ap_config = config
            async_dispatcher_send(self.hass, f"{DOMAIN}_ap_config_update")

    @staticmethod
    def _get_wakeup_reason_string(reason: int) -> str:
        """Convert numeric wakeup reason code to human-readable string.

        Maps the numeric reasons received from the AP to descriptive strings:

        - 0: "TIMED" (normal timed wakeup)
        - 1: "BOOT" (device boot)
        - 2: "GPIO" (GPIO trigger)
        - 3: "NFC" (NFC scan)
        - 4: "BUTTON1" (button 1 pressed)
        - 5: "BUTTON2" (button 2 pressed)
        - 6: "BUTTON3" (button 3 pressed)
        - 7: "BUTTON4" (button 4 pressed)
        - 8: "BUTTON5" (button 5 pressed)
        - 9: "BUTTON6" (button 6 pressed)
        - 10: "BUTTON7" (button 7 pressed)
        - 11: "BUTTON8" (button 8 pressed)
        - 12: "BUTTON9" (button 9 pressed)
        - 13: "BUTTON10" (button 10 pressed)
        - 252: "FIRSTBOOT" (first boot)
        - 253: "NETWORK_SCAN" (network scan)
        - 254: "WDT_RESET" (watchdog reset)

        Args:
            reason: Numeric wakeup reason code from the tag

        Returns:
            str: Human-readable reason or "UNKNOWN_{code}" if not recognized
        """
        reasons = {
            0: "TIMED",
            1: "BOOT",
            2: "GPIO",
            3: "NFC",
            4: "BUTTON1",
            5: "BUTTON2",
            6: "BUTTON3",
            7: "BUTTON4",
            8: "BUTTON5",
            9: "BUTTON6",
            10: "BUTTON7",
            11: "BUTTON8",
            12: "BUTTON9",
            13: "BUTTON10",
            252: "FIRSTBOOT",
            253: "NETWORK_SCAN",
            254: "WDT_RESET"
        }
        return reasons.get(reason, f"UNKNOWN_{reason}")

    @staticmethod
    def _get_ap_state_string(state: int) -> str:
        """Convert AP state code to human-readable string.

        Maps the numeric state codes received from the AP to descriptive strings:

        - 0: "Offline"
        - 1: "Online"
        - 2: "Flashing"
        - 3: "Waiting for reset"
        - etc.

        Args:
            state: Numeric AP state code

        Returns:
            str: Human-readable state or "Unknown: {code}" if not recognized
        """
        states = {
            0: "Offline",
            1: "Online",
            2: "Flashing",
            3: "Waiting for reset",
            4: "Requires power cycle",
            5: "Failed",
            6: "Coming online",
            7: "No radio"
        }
        return states.get(state, f"Unknown: {state}")

    @staticmethod
    def _get_ap_run_state_string(state: int) -> str:
        """Convert AP run state code to human-readable string.

        Maps the numeric run state codes received from the AP to descriptive strings:

        - 0: "Stopped"
        - 1: "Paused"
        - 2: "Running"
        - 3: "Initializing"

        The run state indicates the operational mode of the AP's tag update system.

        Args:
            state: Numeric AP run state code

        Returns:
            str: Human-readable run state or "Unknown: {state}" if not recognized
        """
        states = {
            0: "Stopped",
            1: "Paused",
            2: "Running",
            3: "Initializing",
        }
        return states.get(state, f"Unknown: {state}")

    @staticmethod
    def _get_content_mode_string(mode: int) -> str:
        """Convert content mode code to human-readable string.

        Maps the numeric content mode codes to descriptive strings indicating
        what type of content the tag is displaying:

        - 0: "Not configured"
        - 1: "Current date"
        - 7: "Image URL"
        - 25: "Home Assistant"
        - etc.

        Args:
            mode: Numeric content mode code

        Returns:
            str: Human-readable content mode or "Unknown: {mode}" if not recognized
        """
        modes = {
            0: "Not configured",
            1: "Current date",
            2: "Count days",
            3: "Count hours",
            4: "Current weather",
            5: "Firmware update",
            7: "Image URL",
            8: "Weather forecast",
            9: "RSS Feed",
            10: "QR Code",
            11: "Google calendar",
            12: "Remote content",
            14: "Set NFC URL",
            15: "Custom LUT",
            16: "Buienradar",
            18: "Tag Config",
            19: "JSON template",
            20: "Display a copy",
            21: "AP Info",
            22: "Static image",
            23: "Image preload",
            24: "External image",
            25: "Home Assistant",
            26: "Timestamp",
            27: "Dayahead prices",


        }
        return modes.get(mode, f"Unknown: {mode}")

    @property
    def tags(self) -> list[str]:
        """Return list of known tag MAC addresses.

        Provides access to the current set of tracked tag MAC addresses,
        excluding those that have been blacklisted.

        Returns:
            list[str]: List of MAC addresses for all known, non-blacklisted tags
        """
        return list(self._known_tags)

    def get_tag_data(self, tag_mac: str) -> dict:
        """Get the current data for a specific tag.

        Retrieves the complete tag data dictionary for the specified
        tag MAC address, containing all properties like battery level,
        temperature, status, etc.

        Args:
            tag_mac: MAC address of the tag

        Returns:
            dict: Complete tag data dictionary or empty dict if tag not found
        """
        return self._data.get(self._tag_keys.get(tag_mac.upper(), tag_mac), {})

    def get_blacklisted_tags(self) -> list[str]:
        """Return the list of blacklisted tag MAC addresses.

        Blacklisted tags are known to the AP but ignored by Home Assistant.
        This is configured through the integration's options flow.

        Returns:
            list[str]: List of blacklisted tag MAC addresses
        """
        return self._blacklisted_tags

    def get_discovered_hubs(self) -> dict[str, dict[str, Any]]:
        """Return discovery records with live status kept outside the store."""
        return {host: self.get_discovered_hub(host) for host in self._discovered_hubs}

    def get_discovered_hub(self, hub_id: str) -> dict[str, Any]:
        """Return the secondary AP's metadata and current connection status."""
        return {
            **self._discovered_hubs.get(hub_id, {}),
            "online": self.is_ap_online(hub_id),
            "status": self._secondary_status.get(hub_id, {}).copy(),
        }

    @property
    def ap_status(self) -> dict:
        """Get current AP status information.

        Returns a copy of the current AP status dictionary containing:

        - Connection information (IP, Wi-Fi settings)
        - System metrics (heap, database size)
        - Operational state (uptime, run state)
        - Tag statistics (record count, low battery count)

        Returns:
            dict: Copy of the current AP status dictionary
        """
        return self._ap_data.copy()

    async def async_update_ap_config(self) -> None:
        """Force an update of AP configuration from the AP.

        Fetches the current AP configuration settings via HTTP and
        updates the internal configuration state. This will trigger
        updates for any entities that display configuration values.

        Raises:
            HomeAssistantError: If the AP is offline or returns an error.
        """
        try:
            await self._read_ap_config(self.host)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Could not refresh AP configuration: %s", err)

    @staticmethod
    def _calculate_runtime_delta(new_data: dict, existing_data: dict) -> int:
        """Calculate a tag's runtime delta between check-ins.

        Determines how much runtime to add based on the difference
        between last_seen timestamps, taking into account:

        - Power cycles (resets runtime counter)
        - Invalid intervals (exceeding max_valid_interval)

        Args:
            new_data: New tag data received from AP
            existing_data: Previously stored tag data

        Returns:
            int: Runtime in seconds to add to the tag's total runtime,
                 or 0 if the interval is invalid or a power cycle occurred
        """
        last_seen_old = existing_data.get("last_seen") or 0
        last_seen_new = new_data.get("lastseen", 0)

        if last_seen_old == 0:
            return 0

        time_diff = last_seen_new - last_seen_old
        max_valid_interval = 600  # 10 minutes - max expected interval between check-ins

        wake_reason = new_data.get("wakeupReason")
        is_power_cycle = wake_reason in [1, 252, 254]  # BOOT, FIRSTBOOT, WDT_RESET

        if is_power_cycle or not 0 < time_diff <= max_valid_interval:
            return 0

        return time_diff

    def _build_storage_payload(self) -> dict[str, Any]:
        """Build persisted storage payload."""
        return {
            "tags": self._data,
            "discovered_hubs": self._discovered_hubs,
        }

    def _upsert_discovered_hub(self, evidence: dict[str, Any], source: str) -> None:
        """Create/update discovered remote hub records."""
        hub_id = evidence.get("hub_id")
        ip = evidence.get("ip")
        if not hub_id or not ip:
            return

        now = datetime.now(timezone.utc).isoformat()
        metadata = evidence.get("metadata", {})
        evidence_path = evidence.get("evidence_path", "unknown")
        existing = self._discovered_hubs.get(hub_id)

        if existing is None:
            self._discovered_hubs[hub_id] = {
                "hub_id": hub_id,
                "ip": ip,
                "last_seen": now,
                "discovery_sources": [source],
                "metadata": metadata,
                "evidence_path": evidence_path,
            }
            _LOGGER.info("Discovered remote AP hub %s via %s (evidence: %s)", hub_id, source, evidence_path)
            async_dispatcher_send(self.hass, SIGNAL_REMOTE_AP_DISCOVERED, hub_id)
            async_dispatcher_send(self.hass, SIGNAL_AP_UPDATE)
            self._schedule_storage_save()
            return

        updated = False
        existing["last_seen"] = now
        if source not in existing.get("discovery_sources", []):
            existing.setdefault("discovery_sources", []).append(source)
            updated = True
        if metadata and metadata != existing.get("metadata"):
            existing["metadata"] = metadata
            updated = True
        if evidence_path != existing.get("evidence_path"):
            existing["evidence_path"] = evidence_path
            updated = True

        if updated:
            _LOGGER.info("Updated remote AP hub metadata for %s via %s", hub_id, source)
            async_dispatcher_send(self.hass, SIGNAL_AP_UPDATE)
            self._schedule_storage_save()

    @staticmethod
    def _format_ap_model(ap_env: str) -> str:
        """Format the build string to a user-friendly display name.

        Converts technical model identifiers received from the AP
        into human-readable device model names for display in the UI.

        For example:

        - "OpenEPaper_Mini_AP_v4" becomes "Mini AP v4"
        - "ESP32_S3_16_8_YELLOW_AP" becomes "Yellow AP"

        Args:
            ap_env: The raw build environment string from the AP

        Returns:
            str: Human-friendly model name if known, or the original
                 string if no mapping exists. Returns "ESP32" if input is empty.
        """
        if not ap_env:
            return "ESP32"

        model_mapping = {
            "ESP32_S3_C6_NANO_AP": "Nano AP",
            "OpenEPaperLink_Mini_AP_v4": "Mini AP v4",
            "OpenEPaperLink_ESP32-PoE-ISO_AP": "PoE ISO AP",
            "ESP32_S3_16_8_LILYGO_AP": "LilyGo T-Panel S3",
            "OpenEPaperLink_AP_and_Flasher": "AP and Flasher",
            "OpenEPaperLink_PoE_AP": "PoE AP",
            "BLE_ONLY_AP": "BLE only AP",
            "OpenEPaperLink_Nano_TLSR": "Nano TLSR AP",
            "ESP32_S3_16_8_YELLOW_AP": "Yellow AP",
        }

        if ap_env in model_mapping:
            return model_mapping[ap_env]

        return ap_env

    def is_tag_online(self, tag_mac: str) -> bool:
        """Check if a tag is online based on AP's timeout logic.

        Uses AP timeout formula: maxsleep * 60 + 300 seconds.

        Args:
            tag_mac: MAC address of the tag.

        Returns:
            True if the tag is online, False if timed out or not found.
        """
        if tag_mac not in self.tags:
            return False

        tag_data = self.get_tag_data(tag_mac)
        last_seen = tag_data.get("last_seen") or 0

        if last_seen == 0:
            return False

        modecfgjson = tag_data.get("modecfgjson")
        maxsleep = 60  # Default

        if modecfgjson and isinstance(modecfgjson, dict):
            maxsleep = modecfgjson.get("maxsleep", 60)

        timeout_threshold = (maxsleep * 60) + 300
        current_time = datetime.now(timezone.utc).timestamp()

        return (current_time - last_seen) < timeout_threshold
