import pytest
from types import SimpleNamespace
from custom_components.open_epaper_link.util import get_hub_for_tag
from custom_components.open_epaper_link.const import DOMAIN

class DummyHub:
    def __init__(self, host, data=None):
        self.host = host
        self._data = data or {}
    def get_tag_data(self, mac):
        return self._data.get(mac.upper())

@pytest.fixture
def hass_mock():
    return SimpleNamespace(data={DOMAIN: {}})

@pytest.fixture
def hubs(hass_mock):
    hub1 = DummyHub("ap1", {"AA": {"ap_ip": "ap1"}})
    hub2 = DummyHub("ap2")
    hass_mock.data[DOMAIN] = {"1": hub1, "2": hub2}
    return hub1, hub2

def test_lookup_returns_matching_hub(hass_mock, hubs):
    hub1, _ = hubs
    assert get_hub_for_tag(hass_mock, "AA") is hub1

def test_lookup_after_roaming(hass_mock, hubs):
    hub1, hub2 = hubs
    hub1._data["AA"]["ap_ip"] = "ap2"
    hub2._data["AA"] = {"ap_ip": "ap2"}
    assert get_hub_for_tag(hass_mock, "AA") is hub2

def test_fallback_to_first_hub(hass_mock, hubs):
    hub1, hub2 = hubs
    hub1._data.pop("AA")
    assert get_hub_for_tag(hass_mock, "AA") is hub1

