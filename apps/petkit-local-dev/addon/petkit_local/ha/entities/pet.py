"""HA sensors for a per-pet virtual device — the pet, not the hardware.

A household with two cats and one litter box wants per-cat history, which no
device entity can express. `ai/pets.py::PetRegistry` owns the pets and
`ha/publisher.py` publishes each as its own HA device (identifiers
`petkit_pet_{id}`, a namespace of its own so pet ids cannot collide with device
ids) using the list below. Modeled on Jezza34000/homeassistant_petkit's pet
sensors.

Unlike every other list here these do NOT go through `ha/categories.py`:
they belong to no device type, and unlike a device the values are not reported
by anything — `publish_pet_state` recomputes all of them from the event store.
"""
from petkit_local.ha.discovery import EntityDef

PET_SENSORS = [
    EntityDef(component="sensor", key="last_visit", name="Last Visit",
              value_path="state.lastVisit", device_class="timestamp", icon="mdi:cat"),
    EntityDef(component="sensor", key="visits_today", name="Visits Today",
              value_path="state.visitsToday", icon="mdi:counter"),
    EntityDef(component="sensor", key="last_visit_weight", name="Last Visit Weight",
              value_path="state.lastVisitWeight", device_class="weight", unit="g",
              icon="mdi:scale-bathroom"),
    EntityDef(component="sensor", key="last_visit_duration", name="Last Visit Duration",
              value_path="state.lastVisitDuration", unit="s", device_class="duration",
              icon="mdi:timer-outline"),
    EntityDef(component="sensor", key="last_device_used", name="Last Device Used",
              value_path="state.lastDeviceUsed", icon="mdi:home-floor-a"),
]
