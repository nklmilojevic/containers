"""Home Assistant integration: MQTT discovery, state publishing and commands.

This package owns everything on the HA side of the add-on and nothing on the
device side. `discovery.py` declares the entity model and renders discovery
payloads, `entities/` holds the per-category entity lists, `publisher.py` runs
the one connection to HA's broker, and `commands.py` turns a write from HA back
into a device command. The device conversation itself lives in `http/` and
`mqtt/`.
"""
