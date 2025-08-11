# Multi-Hub Requirements

This document tracks the minimum features needed to support multiple OpenEpaperLink hubs within a single Home Assistant configuration entry.

## Features

1. **Per hub WebSocket** – every hub keeps an independent WebSocket connection.  The main hub starts first and any new hub discovered via tag data spawns its own task.
2. **Hub device creation** – each hub appears as a single device in Home Assistant.  All AP related sensors for that host belong to that device rather than creating additional config entries.
3. **Tag connection tracking** – the integration remembers which hub IP each tag currently uses.  A "Connected Hub" sensor exposes this value for each tag.  Service calls (like `drawcustom` or `setled`) use this IP when sending commands.
4. **Update merging** – updates coming from different hubs are merged.  Data from the hub that the tag is directly associated with takes priority so duplicate messages don't create churn.
5. **Automatic hub discovery** – when a tag message references another AP, the integration creates a WebSocket connection to that hub and sets up the same sensors under a new device without a new config entry.
6. **Logging** – hub discovery, connection state transitions and tag hand‑offs are logged at `INFO` level.  Errors during connection or communication are logged at `ERROR` level.  Ignored updates from secondary hubs are logged at `DEBUG` level for troubleshooting.

## Failure modes and edge cases

* Hubs may disappear from the network.  Their WebSocket reconnect logic should keep trying without spamming the logs.
* Tags can roam between hubs.  When a hub reports a tag as directly connected the stored hub IP must be updated even if another hub previously owned it.
* Multicast messages may arrive out of order.  Ignore external updates if we already have a direct update from another hub.
* When no direct hub is known yet, accept updates from any hub so that tags still appear on first discovery.

