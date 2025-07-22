# Multi-AP Support & Reliability Improvement Requirements

## Overview
The integration must handle multiple OpenEPaperLink Access Points (APs) and provide robust routing and update logic.

## Goals
- Allow simultaneous connections to multiple APs.
- Track which AP each tag is connected to via `apip`.
- Route commands and uploads to the proper AP.
- Prioritize direct updates while falling back to external ones after a timeout.
- Include request timeouts and proper task management.

## Key Functional Points
1. Store `apip` for each tag and normalise `0.0.0.0` to the hub host.
2. Provide `get_hub_for_tag(mac)` helper to locate the correct hub.
3. Maintain an upload queue per hub and route service helpers through this lookup.
4. Ignore duplicate updates from external APs unless the main AP has been silent for the configurable **External Update Timeout**.
5. Include a timeout of 10 seconds on all outgoing HTTP requests.

Refer to the repository's README for additional setup and usage details.

