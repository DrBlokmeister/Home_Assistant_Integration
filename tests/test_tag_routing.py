from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

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

spec = importlib.util.spec_from_file_location(
    "custom_components.open_epaper_link.coordinator",
    PACKAGE_ROOT / "coordinator.py",
)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["custom_components.open_epaper_link.coordinator"] = module
spec.loader.exec_module(module)
Hub = module.Hub


def _build_hub(host: str = "10.0.20.73") -> Hub:
    """Create a minimal Hub object for unit testing helper behavior."""
    hub = Hub.__new__(Hub)
    hub.host = host
    hub._data = {}
    return hub


def test_resolve_tag_target_host_uses_valid_connected_ap() -> None:
    hub = _build_hub()
    hub._data["AA11BB22CC33"] = {"connected_ap": "10.0.20.74"}

    target_host, fallback_used = hub.resolve_tag_target_host(
        action="tag_cmd:refresh",
        entity_id="open_epaper_link.aa11bb22cc33",
    )

    assert target_host == "10.0.20.74"
    assert fallback_used is False


def test_resolve_tag_target_host_falls_back_when_missing_connected_ap() -> None:
    hub = _build_hub(host="10.0.20.73")
    hub._data["AA11BB22CC33"] = {}

    target_host, fallback_used = hub.resolve_tag_target_host(
        action="setled",
        entity_id="open_epaper_link.aa11bb22cc33",
    )

    assert target_host == "10.0.20.73"
    assert fallback_used is True


def test_resolve_tag_target_host_falls_back_when_invalid_connected_ap() -> None:
    hub = _build_hub(host="10.0.20.73")
    hub._data["AA11BB22CC33"] = {"connected_ap": "invalid-hostname"}

    target_host, fallback_used = hub.resolve_tag_target_host(
        action="imgupload",
        entity_id="open_epaper_link.aa11bb22cc33",
    )

    assert target_host == "10.0.20.73"
    assert fallback_used is True


def test_send_tag_cmd_passes_resolved_target_host() -> None:
    hub = _build_hub(host="10.0.20.73")
    hub._data["AA11BB22CC33"] = {"connected_ap": "10.0.20.90"}
    hub._ap_request = AsyncMock(return_value=SimpleNamespace(status_code=200))

    asyncio.run(hub.send_tag_cmd("open_epaper_link.aa11bb22cc33", "refresh"))

    hub._ap_request.assert_awaited_once()
    _, kwargs = hub._ap_request.await_args
    assert kwargs["target_host"] == "10.0.20.90"
    assert kwargs["data"] == {"mac": "AA11BB22CC33", "cmd": "refresh"}
