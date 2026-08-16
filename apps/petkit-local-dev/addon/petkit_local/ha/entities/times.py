"""HA `time` entities — the settings that are a time of day.

The device stores these as SECONDS SINCE MIDNIGHT (43200 is noon), while Home
Assistant's MQTT time platform speaks `HH:MM:SS`. The translation lives in two
places and nowhere else: `ha/discovery.py::_value_template` renders the stored
number for HA, and `ha/commands.py::_coerce_time` parses HA's clock back into a
number for the device.

This is a different shape from the `*MultiRange` schedules, which are RANGES in
MINUTES since midnight and are edited in the web panel — HA has no entity for a
list of ranges. One point in time is the only part of PetKit's scheduling model
that maps onto an HA entity at all.

`ha/categories.py` decides which of these lists a given device type gets.
"""
from petkit_local.ha.discovery import EntityDef

#: When the W7H runs each of its two water-treatment cycles.
#:
#: Encoding confirmed by a capture of the app's own writes (map supplied
#: 2026-08-09): `flushTime` 46800 for 13:00 and 29700 for 08:15,
#: `waterChangeTime` 43200 for 12:00. Seconds, not the minutes every range
#: field on these devices uses — the two encodings sit side by side in one
#: settings block, which is precisely why this is written down.
#:
#: How OFTEN each runs is the matching `number` in
#: `numbers.py::FOUNTAIN_W7H_NUMBERS`; neither half means anything without the
#: other, so they are added together.
FOUNTAIN_W7H_TIMES = [
    EntityDef(component="time", key="water_change_time", name="Drain & Refill Time",
              value_path="settings.waterChangeTime", icon="mdi:water-refresh"),
    EntityDef(component="time", key="flush_time", name="Drain & Flush Time",
              value_path="settings.flushTime", icon="mdi:water-sync"),
]
