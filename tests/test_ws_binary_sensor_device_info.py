import types

from custom_components.open_epaper_link.binary_sensor import (
    OpenEPaperLinkWSBinarySensor,
)
from custom_components.open_epaper_link.const import DOMAIN


def test_primary_hub_uses_shared_identifier():
    hub = types.SimpleNamespace(
        host="10.0.20.73",
        entry=types.SimpleNamespace(entry_id="entry", title="Study AP"),
        ap_model="ESP32",
        is_online=lambda host: True,
    )
    sensor = OpenEPaperLinkWSBinarySensor(hub, hub.host)
    info = sensor.device_info
    assert info["identifiers"] == {(DOMAIN, "ap")}
    assert info["name"] == "Study AP"


def test_secondary_hub_uses_host_identifier():
    hub = types.SimpleNamespace(
        host="10.0.20.73",
        entry=types.SimpleNamespace(entry_id="entry", title="Study AP"),
        ap_model="ESP32",
        is_online=lambda host: True,
    )
    sensor = OpenEPaperLinkWSBinarySensor(hub, "10.0.20.74")
    info = sensor.device_info
    assert info["identifiers"] == {(DOMAIN, "ap_10.0.20.74")}
    assert info["name"] == "OpenEPaperLink AP 10.0.20.74"
