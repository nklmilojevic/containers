"""The panel application's `app[...]` contract, in one place.

`create_panel_app` builds one `web.Application` and hangs every collaborator on
it; each handler in `web/api/` then reads what it needs back out. That contract
used to exist only as a string typed in eleven places and read in ten modules,
which is exactly the kind of thing that drifts silently — a misspelt read is a
`KeyError` at request time and a misspelt `.get` is a `None` that reads as "not
wired".

Two of the thirteen keys are set from OUTSIDE the constructor, appear in no
signature, and are the reason this file lists all of them rather than only what
`create_panel_app` writes.

===================  ==========================================  ===============
key                  what it holds                               set by
===================  ==========================================  ===============
`registry`           `DeviceRegistry` — every real device        create_panel_app
`ble_registry`       `BLERegistry` — the BLE accessories, or     create_panel_app
                     None when there is none
`hub`                `EventHub` — the live log ring, the         create_panel_app
                     WebSocket fan-out and the diagnostics
`cfg`                the static panel config dict (`api_url`,    create_panel_app
                     `media_root`, `capture_dir`, `data_dir`,
                     `settings_path`, ...), read per request
`bridge`             `MQTTBridge` — the device-facing publish    create_panel_app
                     path, or None with `--no-mqtt`
`live_config`        the SAME dict the device-facing HTTP        create_panel_app
                     handlers read, so a settings change from
                     the panel needs no restart. `{}` in tests
`event_store`        `EventStore`, or None                       create_panel_app
`retention_config`   `RetentionConfig`, or None                  create_panel_app
`pet_registry`       `PetRegistry`, or None                      create_panel_app
`ha_publisher`       `HAPublisher`, or None (`--no-ha`)          create_panel_app
`background_tasks`   the strong references to in-flight          create_panel_app
                     background tasks (see `_spawn_background`)
`mqtt_broker`        the embedded amqtt broker, for the          main/lifecycle.py
                     delivery view. Injected because the
                     broker starts after the panel is built
`go2rtc`             the camera sidecar, for a device's RTSP     main/lifecycle.py
                     URL. Injected so a test that builds a
                     panel need not know about it
===================  ==========================================  ===============

Both injected keys are set BEFORE `runner.setup()`, which freezes the
Application: a key added after that is a DeprecationWarning today and an error
under aiohttp 4.

These are plain strings rather than `web.AppKey`s. An `AppKey` is a distinct
object, not the string, so adopting one would change the identity of every key —
including the two written from `main/lifecycle.py` and the ones every test's own
app is built with — for a type-checking benefit this codebase does not collect.
The values below are therefore the literal spellings already in use, and reads
spell them out as they always have.
"""
from __future__ import annotations

REGISTRY = "registry"
BLE_REGISTRY = "ble_registry"
HUB = "hub"
CFG = "cfg"
BRIDGE = "bridge"
LIVE_CONFIG = "live_config"
EVENT_STORE = "event_store"
RETENTION_CONFIG = "retention_config"
PET_REGISTRY = "pet_registry"
HA_PUBLISHER = "ha_publisher"

# aiohttp app key holding the strong references to in-flight background tasks
# (see _spawn_background).
BACKGROUND_TASKS = "background_tasks"

#: Set from `main/lifecycle.py`, not from `create_panel_app`.
MQTT_BROKER = "mqtt_broker"
GO2RTC = "go2rtc"
