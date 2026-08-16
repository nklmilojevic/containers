"""petkit-local: a local replacement for the PetKit cloud, as an HA add-on.

A PetKit device is pointed at this add-on instead of `api.eu-pet.com`, and the
packages here answer it exactly as the official server would: `http/` serves the
device's REST conversation, `mqtt/` embeds the Aliyun-IoT-flavoured broker it
connects to, `devices/` holds the model and registry both of those mutate, and
`ha/` turns that state into Home Assistant entities over HA's own broker.
Everything else supports those four: `media/` and `events/` for what the camera
models upload, `ai/` for the pet face photos the device's NPU matches against,
`web/` for the management panel, `patchers/` for the on-device binary patches,
and `utils/` for the shared, PetKit-agnostic helpers.

Runs as one asyncio event loop in one container, with no database beyond a
SQLite event store and JSON files. `main.py` is the entry point and the only
module that wires the parts together.
"""
