from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path("custom_components/open_epaper_link/hub_discovery.py")
spec = importlib.util.spec_from_file_location("hub_discovery", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

extract_remote_hub_evidence = module.extract_remote_hub_evidence
resolve_connected_ap = module.resolve_connected_ap


def test_extract_remote_hub_evidence_from_nested_payload() -> None:
    payload = {
        "sys": {"ip": "192.168.1.10"},
        "mesh": {
            "nodes": [
                {"hub_ip": "192.168.1.11", "model": "Mini AP"},
                {"ap_ip": "192.168.1.12", "name": "Remote AP"},
            ]
        },
    }

    evidence = extract_remote_hub_evidence(payload, main_hub_ip="192.168.1.10")
    hub_ids = {item["hub_id"] for item in evidence}

    assert hub_ids == {"192.168.1.11", "192.168.1.12"}


def test_extract_remote_hub_evidence_ignores_non_ip_values() -> None:
    payload = {
        "sys": {"ip": "main-hub.local"},
        "remote": {"hub": "not-an-ip", "host": "mesh.local"},
    }

    evidence = extract_remote_hub_evidence(payload, main_hub_ip="192.168.1.10")

    assert evidence == []


def test_resolve_connected_ap_zero_resolves_to_main_hub_ip() -> None:
    connected_ap, source = resolve_connected_ap("0.0.0.0", main_hub_ip="10.0.20.73")

    assert connected_ap == "10.0.20.73"
    assert source == "tag.apip_zero_fallback_main_hub"


def test_resolve_connected_ap_non_zero_uses_tag_value() -> None:
    connected_ap, source = resolve_connected_ap("10.0.20.74", main_hub_ip="10.0.20.73")

    assert connected_ap == "10.0.20.74"
    assert source == "tag.apip"
