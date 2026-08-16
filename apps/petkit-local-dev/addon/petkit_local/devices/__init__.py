"""The device model: what a connected PetKit device is and what it publishes.

`base.py` is the single source of every payload the device is answered with,
`registry.py` owns the in-memory set of devices plus its crash-safe persistence,
`state_parsers.py` normalises the wildly different state reports into one key
space, `categories.py` maps a codename to the HA entities and MQTT topics its
family exposes, and `ble.py` covers the accessories that have no network of
their own and reach us through a WiFi device.

This package deliberately knows nothing about transport: `http/` and `mqtt/`
call into it, never the other way round.
"""
