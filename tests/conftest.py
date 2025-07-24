import sys
import types
from types import SimpleNamespace

# Minimal stub modules for Home Assistant used in tests
ha = types.ModuleType('homeassistant')
core = types.ModuleType('homeassistant.core')
class ServiceCall: pass
class HomeAssistant: pass
core.HomeAssistant = HomeAssistant
core.ServiceCall = ServiceCall
core.CALLBACK_TYPE = object
core.callback = lambda func: func

helpers = types.ModuleType('homeassistant.helpers')
# dispatcher
helpers.dispatcher = types.ModuleType('homeassistant.helpers.dispatcher')
async def async_dispatcher_send(*args, **kwargs):
    pass
helpers.dispatcher.async_dispatcher_send = async_dispatcher_send
# aiohttp client
helpers.aiohttp_client = types.ModuleType('homeassistant.helpers.aiohttp_client')
async def async_get_clientsession(hass):
    return None
helpers.aiohttp_client.async_get_clientsession = async_get_clientsession
# storage
helpers.storage = types.ModuleType('homeassistant.helpers.storage')
class Store:
    def __init__(self, *args, **kwargs):
        pass
helpers.storage.Store = Store
# device registry
helpers.device_registry = types.ModuleType('homeassistant.helpers.device_registry')
helpers.device_registry.async_get = lambda hass: SimpleNamespace(async_get_device=lambda identifiers=None: None, async_entries_for_label=lambda registry,label: [])
# entity registry
helpers.entity_registry = types.ModuleType('homeassistant.helpers.entity_registry')
helpers.entity_registry.async_get = lambda hass: SimpleNamespace(async_update_entity=lambda *a, **k: None, async_get=lambda *a, **k: None, async_get_device=None)
# network
helpers.network = types.ModuleType('homeassistant.helpers.network')
helpers.network.get_url = lambda hass: "http://localhost"

exceptions = types.ModuleType('homeassistant.exceptions')
class HomeAssistantError(Exception):
    pass
exceptions.HomeAssistantError = HomeAssistantError

config_entries = types.ModuleType('homeassistant.config_entries')
class ConfigEntry:
    def __init__(self, entry_id=None, data=None, options=None):
        self.entry_id = entry_id
        self.data = data or {}
        self.options = options or {}
config_entries.ConfigEntry = ConfigEntry

ha.core = core
ha.helpers = helpers
ha.exceptions = exceptions
ha.config_entries = config_entries

sys.modules.setdefault('homeassistant', ha)
sys.modules.setdefault('homeassistant.core', core)
sys.modules.setdefault('homeassistant.helpers', helpers)
sys.modules.setdefault('homeassistant.helpers.dispatcher', helpers.dispatcher)
sys.modules.setdefault('homeassistant.helpers.aiohttp_client', helpers.aiohttp_client)
sys.modules.setdefault('homeassistant.helpers.storage', helpers.storage)
sys.modules.setdefault('homeassistant.helpers.device_registry', helpers.device_registry)
sys.modules.setdefault('homeassistant.helpers.entity_registry', helpers.entity_registry)
sys.modules.setdefault('homeassistant.helpers.network', helpers.network)
sys.modules.setdefault('homeassistant.exceptions', exceptions)
sys.modules.setdefault('homeassistant.config_entries', config_entries)

const = types.ModuleType('homeassistant.const')
class Platform:
    SENSOR = 'sensor'
    BUTTON = 'button'
    CAMERA = 'camera'
    SELECT = 'select'
    SWITCH = 'switch'
    TEXT = 'text'
EVENT_HOMEASSISTANT_STARTED = 'ha_started'
EVENT_HOMEASSISTANT_STOP = 'ha_stop'
const.Platform = Platform
const.EVENT_HOMEASSISTANT_STARTED = EVENT_HOMEASSISTANT_STARTED
const.EVENT_HOMEASSISTANT_STOP = EVENT_HOMEASSISTANT_STOP
const.CONF_HOST = 'host'
ha.const = const
sys.modules.setdefault('homeassistant.const', const)
