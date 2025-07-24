# Multi-AP Support & Reliability Improvement Requirements

This document describes how the integration handles multiple OpenEPaperLink Access Points (APs) and tracks the status of each requirement.

## Goals
- [x] Allow simultaneous connections to multiple APs.
- [x] Track which AP each tag is connected to via `apip`.
- [x] Route commands and uploads to the correct AP based on the stored address.
- [x] Prioritize direct updates while falling back to external ones after a timeout.
- [x] Include request timeouts and proper task management.

## Key Concepts
- **AP (Access Point)**: An ESP32-based OpenEPaperLink device hosting tags.
- **Tag**: An electronic shelf label.
- **Connected AP**: The AP currently serving a tag, indicated by `apip` and `isexternal` fields in WebSocket messages.

## Functional Requirements
1. **Multiple Hub Instances**
   - [x] Maintain a `Hub` object for each configured AP.
   - [x] Each hub establishes its own WebSocket connection and manages state for that AP.
2. **Tag–AP Mapping**
   - [x] Store `apip` for each tag and normalise `0.0.0.0` to the hub host.
   - [x] Update the stored `apip` when tags roam to other APs.
3. **Service Routing**
   - [x] Helper functions look up the correct hub via `get_hub_for_tag(mac)`.
4. **Prioritization and Fallback**
   - [x] Prefer updates where `isexternal` is `false`; accept external updates after a configurable timeout.
5. **Upload Queue Handling**
   - [x] Instantiate an `UploadQueueHandler` per hub tied to the Home Assistant lifecycle.
6. **Configuration**
   - [x] Allow multiple AP hosts to be managed under a single integration entry.
   - [x] Automatically discover new APs from tag updates and add them as devices.
7. **Entities**
   - [x] Maintain a single entity per tag and update its `apip` when roaming.
8. **HTTP Request Timeouts**
   - [x] All outgoing HTTP requests include a timeout and raise `HomeAssistantError` on failure.
9. **Documentation and Tests**
   - [x] Document multi-AP setup and duplicate update handling.
   - [x] Unit tests verify hub selection and tag roaming.
10. **README Maintenance**
    - [x] Ensure the README ends with a newline and a complete sentence.

## Non-Functional Requirements
- [x] Backward compatible with single-AP setups.
- [x] Background tasks and network calls clean up on Home Assistant shutdown.
- [x] Code follows Home Assistant guidelines and linting rules.

## Implementation Tasks
- [x] Extend `Hub._process_tag_data` to store `apip`.
- [x] Implement `get_hub_for_tag(mac)` for tag-based routing.
- [x] Route service helpers and image uploads through the correct hub.
- [x] Add prioritization logic using `isexternal` and `apip`.
- [x] Apply HTTP request timeouts to all calls.
- [x] Tie `UploadQueueHandler` to the Home Assistant lifecycle.
- [x] Fix trailing newline in `README.md`.
- [x] Provide tests for tag–hub mapping and roaming scenarios.

