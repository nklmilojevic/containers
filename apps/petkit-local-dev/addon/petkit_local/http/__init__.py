"""The device-facing half of petkit-local: the HTTP cloud a PetKit device sees.

Everything a device sends over HTTP arrives here. `server.py` owns the routing
table and the "never 404 a device" rule, `middleware/` derives who is calling,
`handlers/` implements one PetKit endpoint per module, `bucket.py` is the
S3/OSS-compatible listener the device's `cloud` process PUTs media to, and
`proxy.py` forwards to the official cloud in proxy mode.

Every device starts here — signup and `dev_serverinfo` have no MQTT equivalent —
and one that never reaches the broker stays here for everything, including its
state and event reports. See `mqtt/bridge.py` for what changes when it does.

The package deliberately shadows the stdlib `http` name. Every import in this
codebase is absolute (`petkit_local.http...`) and Python 3 has no implicit
relative imports, so nothing here resolves against it.
"""
