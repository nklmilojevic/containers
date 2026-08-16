"""HA entities for the BLE accessories — the K3 spray and the fountains.

Not selected by `ha/categories.py` like every other list here: an accessory has
no device type to select on, so `get_ble_entities` picks by `ble_type` instead.
Which kinds exist, how their frames decode and which keys can be written back
is `devices/ble/`; only the HA-facing declarations are here.
"""
from petkit_local.devices.ble import W5_PROTOCOL
from petkit_local.ha.discovery import EntityDef

K3_ENTITIES = [
    EntityDef(component="sensor", key="k3_liquid", name="K3 Liquid",
              value_path="consumables.liquid", unit="%", icon="mdi:water-percent"),
    EntityDef(component="sensor", key="k3_battery", name="K3 Battery",
              value_path="consumables.battery", unit="%", device_class="battery",
              icon="mdi:battery"),
]

#: W5 / W4 / CTW2. One protocol serves the whole family, so one list does too.
#:
#: `w5_power` is a switch rather than a binary sensor, and deliberately not
#: both: a switch shows the state AND sets it, so shipping the pair would put
#: the same fact on screen twice.
#:
#: The settings half — light, brightness, do-not-disturb, child lock, both
#: smart-cycle times — is only ever populated by a cmd-211 frame, which nothing
#: here asks for. Until one arrives those entities sit at unknown and a write to
#: them is refused rather than guessed (`w5_config_payload`).
W5_ENTITIES = [
    EntityDef(component="sensor", key="w5_filter", name="Filter",
              value_path="consumables.filterPercentage", unit="%", icon="mdi:filter"),
    EntityDef(component="switch", key="w5_power", name="Power",
              value_path="states.powerStatus", icon="mdi:power"),
    # 0 is off, which is why it is not among the options: this select says which
    # mode the fountain runs in, and the power switch says whether it runs.
    EntityDef(component="select", key="w5_mode", name="Mode",
              value_path="states.mode", icon="mdi:water-pump",
              options=["normal", "smart"], option_values=[1, 2]),
    EntityDef(component="binary_sensor", key="w5_running", name="Pump Running",
              value_path="states.runningStatus", device_class="running",
              icon="mdi:pump"),
    EntityDef(component="binary_sensor", key="w5_water_missing", name="Water Low",
              value_path="states.warningWaterMissing", device_class="problem",
              icon="mdi:water-off"),
    EntityDef(component="binary_sensor", key="w5_filter_warn", name="Filter Warning",
              value_path="states.warningFilter", device_class="problem",
              icon="mdi:filter-remove"),
    EntityDef(component="binary_sensor", key="w5_breakdown", name="Breakdown",
              value_path="states.warningBreakdown", device_class="problem",
              icon="mdi:alert"),
    EntityDef(component="binary_sensor", key="w5_dnd_active", name="Quiet Now",
              value_path="states.dndState", icon="mdi:sleep"),
    EntityDef(component="switch", key="w5_light", name="Light",
              value_path="states.lampRingSwitch", icon="mdi:lightbulb"),
    # A byte, and the protocol notes say so; aavdberg/ha-petkit's slider stops
    # at 10 because that is what their fountains report. Bounding it at what the
    # field can hold means a device that reports 100 is displayed rather than
    # rejected as out of range.
    EntityDef(component="number", key="w5_brightness", name="Brightness",
              value_path="states.lampRingBrightness", icon="mdi:brightness-6",
              min_value=0, max_value=255, step=1, entity_category="config"),
    EntityDef(component="switch", key="w5_dnd", name="Do Not Disturb",
              value_path="states.noDisturbingSwitch", icon="mdi:sleep"),
    EntityDef(component="switch", key="w5_child_lock", name="Child Lock",
              value_path="states.isLock", icon="mdi:lock", entity_category="config"),
    EntityDef(component="number", key="w5_smart_work", name="Smart Mode Run Time",
              value_path="states.smartWorkingTime", unit="min", icon="mdi:timer-outline",
              min_value=1, max_value=60, step=1, entity_category="config"),
    EntityDef(component="number", key="w5_smart_sleep", name="Smart Mode Rest Time",
              value_path="states.smartSleepTime", unit="min", icon="mdi:timer-sand",
              min_value=1, max_value=60, step=1, entity_category="config"),
    EntityDef(component="button", key="w5_reset_filter", name="Reset Filter",
              icon="mdi:filter-check"),
    EntityDef(component="sensor", key="w5_pump_runtime", name="Pump Runtime",
              value_path="states.pumpRuntime", unit="s", device_class="duration",
              icon="mdi:timer-outline", entity_category="diagnostic"),
    EntityDef(component="sensor", key="w5_pump_today", name="Pump Runtime Today",
              value_path="states.todayPumpRunTime", unit="s", device_class="duration",
              icon="mdi:timer-outline", entity_category="diagnostic"),
]


#: `electricStatus` value meaning mains power rather than the battery.
CTW3_ELECTRIC_AC = 2


#: CTW3 (EverSweet Max Cordless). Named after the fields the decoder produces,
#: which are PetKit's own — see the block above `_decode_ctw3_status`.
#:
#: `electricStatus` is a plain sensor, not a binary one: 2 has been observed and
#: nobody knows what 3 would mean. `detectStatus` is the opposite case — the
#: value varies (0x02 seen) but the question it answers is yes/no, so it is a
#: binary sensor with a non-zero test rather than a raw byte.
CTW3_ENTITIES = [
    EntityDef(component="sensor", key="ctw3_filter", name="Filter",
              value_path="consumables.filterPercent", unit="%", icon="mdi:filter"),
    EntityDef(component="sensor", key="ctw3_battery", name="Battery",
              value_path="states.batteryPercent", unit="%", device_class="battery",
              icon="mdi:battery"),
    # Writable. A switch shows the state AND sets it, so these replace the
    # binary sensors they would otherwise duplicate; `ble_command_for` maps the
    # key to the frame.
    EntityDef(component="switch", key="ctw3_power", name="Power",
              value_path="states.powerStatus", icon="mdi:power"),
    EntityDef(component="switch", key="ctw3_working", name="Working",
              value_path="states.suspendStatus", icon="mdi:play-pause"),
    # PetKit's own two names for these, and both other implementations use
    # them. They were "continuous"/"intermittent" here, which describes what
    # the pump does but matches nothing a user can compare against.
    EntityDef(component="select", key="ctw3_mode", name="Flow Mode",
              value_path="states.mode", icon="mdi:water-pump",
              options=["normal", "smart"], option_values=[1, 2]),
    EntityDef(component="switch", key="ctw3_light", name="Light",
              value_path="states.lightSwitch", icon="mdi:lightbulb"),
    EntityDef(component="select", key="ctw3_brightness", name="Brightness",
              value_path="states.brightness", icon="mdi:brightness-6",
              options=["low", "medium", "high"], option_values=[1, 2, 3]),
    EntityDef(component="switch", key="ctw3_dnd", name="Do Not Disturb",
              value_path="states.noDisturbingSwitch", icon="mdi:sleep"),
    EntityDef(component="switch", key="ctw3_child_lock", name="Child Lock",
              value_path="states.childLock", icon="mdi:lock",
              entity_category="config"),
    EntityDef(component="number", key="ctw3_energy_interval", name="Battery Work Time",
              value_path="states.energyInterval", unit="s", icon="mdi:timer-outline",
              min_value=15, max_value=3600, step=15, entity_category="config"),
    EntityDef(component="number", key="ctw3_sleep_time", name="Battery Sleep Time",
              value_path="states.sleepTime", unit="s", icon="mdi:timer-sand",
              min_value=60, max_value=7200, step=60, entity_category="config"),
    EntityDef(component="number", key="ctw3_smart_work", name="Smart Mode Run Time",
              value_path="states.smartWorkingTime", unit="min", icon="mdi:timer-outline",
              min_value=1, max_value=60, step=1, entity_category="config"),
    EntityDef(component="number", key="ctw3_smart_sleep", name="Smart Mode Rest Time",
              value_path="states.smartSleepTime", unit="min", icon="mdi:timer-sand",
              min_value=1, max_value=60, step=1, entity_category="config"),
    EntityDef(component="button", key="ctw3_reset_filter", name="Reset Filter",
              icon="mdi:filter-check"),
    # Read-only: the pump is not something you switch, it is what the fountain
    # is doing right now.
    EntityDef(component="binary_sensor", key="ctw3_running", name="Pump Running",
              value_path="states.runStatus", device_class="running", icon="mdi:pump"),
    EntityDef(component="binary_sensor", key="ctw3_drinking", name="Drinking",
              value_path="states.detectStatus", icon="mdi:cup-water"),
    EntityDef(component="binary_sensor", key="ctw3_water_lack", name="Water Low",
              value_path="states.lackWarning", device_class="problem",
              icon="mdi:water-off"),
    EntityDef(component="binary_sensor", key="ctw3_filter_warn", name="Filter Warning",
              value_path="states.filterWarning", device_class="problem",
              icon="mdi:filter-remove"),
    EntityDef(component="binary_sensor", key="ctw3_breakdown", name="Breakdown",
              value_path="states.breakdownWarning", device_class="problem",
              icon="mdi:alert"),
    EntityDef(component="binary_sensor", key="ctw3_low_battery", name="Low Battery",
              value_path="states.lowBattery", device_class="battery",
              icon="mdi:battery-alert"),
    # 2 is mains, and that is the only value with a meaning behind it
    # (aavdberg/ha-petkit gates its power estimate on it). 0 is taken to be the
    # battery. Anything else renders as the raw number rather than as a label
    # somebody invented.
    EntityDef(component="sensor", key="ctw3_power_source", name="Power Source",
              value_path="states.electricStatus", icon="mdi:power-plug",
              options=["battery", "mains"], option_values=[0, CTW3_ELECTRIC_AC],
              entity_category="diagnostic"),
    EntityDef(component="sensor", key="ctw3_module_status", name="Module Status",
              value_path="states.moduleStatus", icon="mdi:chip",
              entity_category="diagnostic"),
    # `duration` rather than a bare second count: 1056887 says nothing, and
    # both the panel and HA render the class as something readable.
    EntityDef(component="sensor", key="ctw3_pump_runtime", name="Pump Runtime",
              value_path="states.waterPumpRunTime", unit="s",
              device_class="duration", icon="mdi:timer-outline",
              entity_category="diagnostic"),
    EntityDef(component="sensor", key="ctw3_pump_today", name="Pump Runtime Today",
              value_path="states.todayPumpRunTime", unit="s",
              device_class="duration", icon="mdi:timer-outline",
              entity_category="diagnostic"),
    EntityDef(component="sensor", key="ctw3_battery_voltage", name="Battery Voltage",
              value_path="states.batteryVoltage", unit="mV",
              device_class="voltage", entity_category="diagnostic"),
    EntityDef(component="sensor", key="ctw3_supply_voltage", name="Supply Voltage",
              value_path="states.supplyVoltage", unit="mV",
              device_class="voltage", entity_category="diagnostic"),
]


def get_ble_entities(ble_type: str) -> list[EntityDef]:
    """HA entity definitions for a BLE accessory type; empty for an unknown one."""
    if ble_type == "k3":
        return list(K3_ENTITIES)
    if ble_type == "ctw3":
        return list(CTW3_ENTITIES)
    if ble_type in W5_PROTOCOL:
        return list(W5_ENTITIES)
    return []
