# Multi-Hub Implementation Overview

This document provides an overview of how support for multiple Open Epaper Link hubs is implemented in this branch. It also explores alternative approaches that were considered.

## High-Level Summary

- **Single configuration entry** – the integration continues to use one configuration entry for the main hub. Additional hubs are discovered automatically and managed internally.
- **Independent WebSocket connections** – a separate WebSocket is opened for each hub IP. The main hub connects first; additional hubs are spawned when discovered from tag updates.
- **Device per hub** – each hub exposes its own device in Home Assistant with the standard AP sensors (heap, record count, etc.). External hubs reuse the same configuration entry.
- **Tag connection tracking** – the hub class stores which IP a tag last used. A “Connected Hub” sensor reflects this value and service calls target that IP.
- **Merged tag updates** – tag messages from multiple hubs are reconciled. Updates from the directly connected hub take precedence over those relayed by other hubs.
- **Logging and reconnects** – hub discovery, connection status changes and tag hand‑offs are logged. Each hub has its own reconnection loop to handle outages.

## Functional Details

1. **Discovery**
   - Incoming tag messages contain `isexternal` and `apip` fields. If a tag is reported as external, the `apip` value is treated as another hub.
   - When an unknown hub IP is seen, the integration adds it to `discovered_hubs`, starts a WebSocket connection to that host and dispatches a signal so AP sensors can be created for it.

2. **AP Sensors and Devices**
   - For each hub, a `DeviceInfo` block is created using the hub IP. The same set of AP sensors (`record_count`, `heap`, etc.) are instantiated per hub.
   - Sensor entities listen for host‑specific update signals (`open_epaper_link_ap_update_{ip}`) and connection status signals (`open_epaper_link_connection_status_{ip}`).

3. **Tag Updates**
   - `_process_tag_data` stores a `connected_ip` for every tag. If the message originates from a host that is not the stored `connected_ip` and the tag is marked external, the update is ignored.
   - When a tag roams and reports `isexternal=False`, the stored `connected_ip` is updated and future commands are routed to the new hub.

4. **Service Calls**
   - Service helpers (`send_tag_cmd`, image uploads, LED flashes) call `hub.get_tag_data(mac)` to obtain the `connected_ip`. Requests are sent to that host instead of always using the primary hub.

5. **Connection Handling**
   - Each hub’s WebSocket handler monitors connection health and reschedules itself after errors. The primary hub verifies tag records on reconnect; external hubs simply resume listening.

## Alternative Approaches

Several other designs were considered to support multiple hubs:

### Multiple configuration entries per hub
- **Pros**
  - Keeps each hub isolated with its own options and credentials.
  - Home Assistant’s built‑in config flow could manage discovery and removal.
- **Cons**
  - Requires the user to configure every hub manually.
  - Tags roaming between hubs would require complex coordination across entries.
  - Harder to maintain a single, merged view of all tags.

### Manual addition via integration options
- **Pros**
  - Simple to implement: an options form could ask for additional hub IPs.
  - Avoids reliance on multicast tag messages for discovery.
- **Cons**
  - User must know all hub addresses ahead of time.
  - Still needs logic to merge updates and avoid duplicate devices.

### Query main hub for known peers
- **Pros**
  - If the main hub exposes its known access points, discovery becomes deterministic.
  - Could allow immediate connection without waiting for tag traffic.
- **Cons**
  - The current hub firmware does not advertise such an API.
  - Adds coupling to hub internals and might break with firmware updates.

### Passive multicast only
- **Pros**
  - Minimal network chatter, relies entirely on existing multicast messages.
  - No additional connections are made.
- **Cons**
  - Tag commands always go through the currently connected hub, which may be unreachable if a tag moved.
  - Provides no per-hub device in Home Assistant and limited visibility into hub status.

Each approach has trade‑offs. The current implementation opts for automatic discovery via tag messages and a single configuration entry to keep user interaction minimal while still exposing the status of every hub.

