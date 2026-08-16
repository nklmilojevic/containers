"""HA `text` entities for editing raw schedule JSON.

The value is the JSON the device fetches via dev_schedule_get / dev_feed_get.
Editing it in HA writes to device.config[<key>], which those handlers serve.
UX is intentionally basic (a JSON string) — it makes schedules editable
end-to-end without a bespoke UI. Note HA's 255-character cap on a text entity
(enforced in `ha/discovery.py`): a longer schedule has to be edited in the web
panel instead.

`ha/categories.py` decides which of these lists a given device type gets.
"""
from petkit_local.ha.discovery import EntityDef

LITTER_SCHEDULE_TEXT = [
    EntityDef(component="text", key="cleaning_schedule", name="Cleaning Schedule (JSON)",
              value_path="schedule", icon="mdi:calendar-clock",
              entity_category="config"),
]

FEEDER_SCHEDULE_TEXT = [
    EntityDef(component="text", key="feeding_schedule", name="Feeding Schedule (JSON)",
              value_path="feed_schedule", icon="mdi:calendar-clock",
              entity_category="config"),
]
