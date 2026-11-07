"""Validate access-point discovery and identify tag radio routes."""

from ipaddress import IPv4Address, IPv4Network
import re
from typing import Any

_PRIVATE_NETWORKS = tuple(
    IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_MAC = re.compile(r"[0-9a-fA-F]{16}")
_RF_ACTIONS = {"setled", "tag_cmd:reboot", "tag_cmd:scan", "tag_cmd:reset", "tag_cmd:deepsleep"}


def normalize_ip(value: Any) -> str | None:
    """Return a usable IPv4 address, excluding non-unicast destinations."""
    if not isinstance(value, str):
        return None
    try:
        address = IPv4Address(value.strip())
    except ValueError:
        return None
    if address.is_unspecified or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved:
        return None
    return str(address)


def normalize_mac(value: Any) -> str | None:
    """Return the canonical MAC used for AP tag identifiers."""
    return value.upper() if isinstance(value, str) and _MAC.fullmatch(value) else None


def same_discovery_network(source: str | None, candidate: str) -> bool:
    """Limit automatic probing to the reporter's private IPv4 /24."""
    if not normalize_ip(source) or not normalize_ip(candidate):
        return False
    address = IPv4Address(candidate)
    network = IPv4Network(f"{source}/24", strict=False)
    return (
        address in network
        and address not in (network.network_address, network.broadcast_address)
        and any(address in private for private in _PRIVATE_NETWORKS)
    )


def discovery_candidate(item: Any) -> str | None:
    """Accept the AP list frame emitted by the firmware, not arbitrary IP fields."""
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("alias"), str)
        or type(item.get("count")) is not int
        or type(item.get("channel")) is not int
        or not isinstance(item.get("version"), str)
    ):
        return None
    return normalize_ip(item.get("ip"))


def resolve_connected_ap(apip: Any, reporter: str, is_external: Any) -> tuple[str | None, str | None]:
    """Identify the radio from a local report or an external report's AP address."""
    if is_external in (False, 0) or (apip == "0.0.0.0" and is_external is None):
        return reporter, "local"
    if is_external in (True, 1):
        address = normalize_ip(apip)
        if address:
            return address, "tag.apip"
    return None, None


def uses_radio(action: str) -> bool:
    """Return whether the firmware sends the operation over the tag radio."""
    return action in _RF_ACTIONS
