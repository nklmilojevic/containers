"""The deodorant countdowns, which are ours to keep rather than the device's.

A box reports reset timestamps and never a remaining count, and for the N50 it
reports nothing at all — `state_tables.py` records the experiment that
established that. So the replacement date lives in `Device.config`, where it
survives a restart, and the days remaining are recomputed from it after every
state report and served back to the device as readily as to Home Assistant.
"""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from petkit_local.devices.state_tables import (CONSUMABLE_RECORD_KEY, CONSUMABLE_TOTALS,
                                               DEODORANT_TOTAL_DAYS, SPRAY_TOTAL_DAYS)
from petkit_local.utils.coerce import to_float

if TYPE_CHECKING:  # import-cycle-free: `devices.base` imports nothing from here
    from petkit_local.devices.base import Device


def record_consumable_reset(device: Device, which: str,
                            when: float | None = None) -> float | None:
    """Stamp `which` ("n50"/"n60") as replaced now and refresh its countdown.

    Returns:
        The stamp written, or None for a name we do not track — the caller logs
        it rather than guessing which consumable was meant.
    """
    if which not in CONSUMABLE_TOTALS:
        return None
    ts = float(when) if when is not None else time.time()
    device.config.setdefault(CONSUMABLE_RECORD_KEY, {})[which] = ts
    apply_consumable_state(device)
    return ts


def apply_consumable_state(device: Device) -> None:
    """Fill both consumable countdowns, and persist the N60 stamp.

    The N50 has no representation anywhere in the device protocol — see the note
    in `state_tables.py` — so a date we recorded is its ONLY possible source.

    The N60 does have one, and the DEVICE wins: `sprayResetTime` is also moved by
    a reset from PetKit's app, so the box's stamp can be newer than ours. What we
    keep is a copy, and that copy earns its place twice: the countdown survives a
    restart, and `to_device_info` stops echoing a zero back over a live reset
    date during the window before the device has reported.

    Call this AFTER the state parsers, which is where `sprayResetTime` arrives.
    """
    rec = device.config.get(CONSUMABLE_RECORD_KEY)
    if not isinstance(rec, dict):
        rec = {}
        device.config[CONSUMABLE_RECORD_KEY] = rec

    reported = to_float(device.state.get("sprayResetTime"), None)
    if reported:
        rec["n60"] = reported
    else:
        remembered = to_float(rec.get("n60"), None)
        if remembered:
            device.state["sprayResetTime"] = remembered

    for which, (state_key, total) in CONSUMABLE_TOTALS.items():
        left = _days_left_from_reset(rec.get(which), total)
        if left is not None:
            device.state[state_key] = left


def _days_left_from_reset(reset_ts: Any, total_days: int) -> int | None:
    """Compute remaining days from a unix reset timestamp (e.g. sprayResetTime).

    Rounded UP, because a part-used day is still a day you have: 19.5 days
    remaining is "20 days left", and it reaches 0 only when the interval is
    genuinely spent. Truncating instead made a consumable replaced one second
    ago report `total - 1`, so pressing Reset N50 showed 29 of 30 days
    immediately — visibly wrong at the one moment the user is looking.

    `reset_ts` is whatever the device put in the field, hence `to_float`
    rather than a numeric annotation. An isinstance check is not enough:
    `json.loads` accepts bare `Infinity`/`NaN` by default and an int is
    unbounded, so the arithmetic could raise OverflowError on a device payload
    and take the whole state report down with it. `to_float` rejects both, and
    accepts the numeric-string form the field sometimes has.
    """
    ts = to_float(reset_ts, None)
    if not ts:
        return None
    days_since = (time.time() - ts) / 86400
    return max(0, math.ceil(total_days - days_since))


def _extract_consumable_days(body: dict[str, Any], state: dict[str, Any]) -> None:
    """Derive the N60 spray and N50 deodorant countdowns from their reset stamps.

    Shared by BOTH transports on purpose. This lived only in
    `_extract_litter_nested`, which the MQTT property post never reaches, so a
    device that speaks only MQTT — a T5 stops polling the HTTP heartbeat once it
    connects, and may never send a single `dev_state_report` — left both sensors
    permanently empty while reporting a perfectly good `sprayResetTime`.

    A zero stamp is not a fresh cartridge, it is "never reset": `liquidReset` was
    0 in all 685 captured reports of a box whose N50 had never been replaced.
    `_days_left_from_reset` returns None for it and the key is left unset, so HA
    shows unknown rather than a confident zero days remaining.
    """
    spray_left = _days_left_from_reset(body.get("sprayResetTime"), SPRAY_TOTAL_DAYS)
    if spray_left is not None:
        state["sprayLeftDays"] = spray_left

    liquid_left = _days_left_from_reset(body.get("liquidReset"), DEODORANT_TOTAL_DAYS)
    if liquid_left is not None:
        state["deodorantLeftDays"] = liquid_left
