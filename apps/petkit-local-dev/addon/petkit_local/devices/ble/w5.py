"""What a W5 / W4 / CTW2 frame's DATA block means, in both directions.

One protocol serves that whole family (`W5_PROTOCOL` in this package's
`__init__`), so one set of offsets, one decoder and one pair of payload
builders serve it too. The framing around the block is shared and lives in
`framing.py`; CTW3 is a different layout inside the same framing and lives in
`ctw3.py`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from petkit_local.devices.base import Refused
from petkit_local.devices.ble.framing import (
    CMD_DEVICE_STATUS, CMD_GET_CONFIG, CMD_GET_STATE, CMD_SET_CONFIG, CMD_SET_MODE,
    FOUNTAIN_MODES, _iter_ble_frames,
)

if TYPE_CHECKING:
    from petkit_local.devices.ble.registry import BLEDevice

log = logging.getLogger(__name__)

# --- W5 / W4 / CTW2 ---------------------------------------------------------
#
# Offsets from mr-ransel's protocol notes, matching aavdberg/ha-petkit's
# parser field for field. The block is 12 bytes on every firmware, 16 where
# `todayPumpRunTime` is supported (typeCode 2 from firmware 24, the rest from
# 35) and 18 where the smart-cycle times are appended.
_W5_STATUS_STATE_OFFSETS = {
    "powerStatus": 0,
    "mode": 1,
    "dndState": 2,
    "warningBreakdown": 3,
    "warningWaterMissing": 4,
    "warningFilter": 5,
    "runningStatus": 11,
}
_W5_STATUS_FILTER_OFFSET = 10  # filterPercentage, raw byte 0-100 for cmd 230

#: The shortest block that is a status rather than a fragment. Accept anything
#: shorter and a one-byte ACK decodes into a confident `powerStatus`, because
#: each field is emitted whenever its offset happens to be in range.
_W5_MIN_STATUS_LEN = 12


def _decode_w5_status(data: bytes) -> dict[str, dict[str, int]]:
    """Decode a cmd-210/230 DATA block into `{"states": {...}, "consumables": {...}}`.

    The first 12 bytes are the block every firmware sends and are read or the
    frame is dropped. What follows is genuinely optional — older builds stop at
    12 — so those fields are read only when they are there, and their absence
    leaves the previous reading alone rather than publishing a zero.
    """
    if len(data) < _W5_MIN_STATUS_LEN:
        log.warning("W5 status frame is %d bytes, expected at least %d - dropped",
                    len(data), _W5_MIN_STATUS_LEN)
        return {"states": {}, "consumables": {}}

    states = {name: data[off] for name, off in _W5_STATUS_STATE_OFFSETS.items()}
    # Byte 1 is 0 on a fountain that is switched off, and `powerStatus` at byte
    # 0 already says so — so the 0 carries nothing and costs the last real mode.
    if states.get("mode") not in FOUNTAIN_MODES:
        log.debug("W5 reported mode=%s - keeping the last real one", states.get("mode"))
        states.pop("mode", None)
    states["pumpRuntime"] = int.from_bytes(data[6:10], "big")
    consumables = {"filterPercentage": data[_W5_STATUS_FILTER_OFFSET]}

    if len(data) >= 16:
        states["todayPumpRunTime"] = int.from_bytes(data[12:16], "big")
    if len(data) >= 17:
        states["smartWorkingTime"] = data[16]
    if len(data) >= 18:
        states["smartSleepTime"] = data[17]
    return {"states": states, "consumables": consumables}


#: The cmd-211 settings block, `(offset, width)` big-endian. 13 bytes on every
#: firmware that answers at all, 14 where the child lock exists.
#:
#: Nothing here asks for this frame — the parent decides what it polls — so the
#: decoder sits idle until one arrives. It costs nothing to have, and without
#: it a settings write can never be anything but a guess: the block is written
#: whole, so the fields nobody is changing have to come from somewhere.
_W5_CONFIG_BYTE_FIELDS = {
    "smartWorkingTime": 0,       # minutes the pump runs in smart mode
    "smartSleepTime": 1,         # minutes it then rests
    "lampRingSwitch": 2,
    "lampRingBrightness": 3,
    "noDisturbingSwitch": 8,
}
_W5_CONFIG_INT_FIELDS = {
    "lampRingLightUpTime": (4, 2),    # minutes from midnight
    "lampRingGoOutTime": (6, 2),
    "noDisturbingStartTime": (9, 2),
    "noDisturbingEndTime": (11, 2),
}
W5_CONFIG_LEN = 13
W5_CONFIG_LOCK_OFFSET = 13


def _decode_w5_config(data: bytes) -> dict[str, int]:
    """Decode a cmd-211 settings block. Empty for anything shorter than 13 bytes."""
    if len(data) < W5_CONFIG_LEN:
        return {}
    out = {name: data[off] for name, off in _W5_CONFIG_BYTE_FIELDS.items()}
    for name, (off, width) in _W5_CONFIG_INT_FIELDS.items():
        out[name] = int.from_bytes(data[off:off + width], "big")
    if len(data) > W5_CONFIG_LOCK_OFFSET:
        out["isLock"] = data[W5_CONFIG_LOCK_OFFSET]
    return out


def parse_w5_ble_response(content: Any) -> dict[str, dict[str, Any]]:
    """Decode a W5 `ble_response` into the state a `W5_ENTITIES` value_path reads.

    Accepts a structured firmware payload (fields already named) OR the real
    binary frame(s), and merges both. Decoded frames are applied last and
    therefore win on any field both forms carry.

    Returns:
        `{"states": {...}, "consumables": {...}}`, matching the `value_path`
        prefixes in `W5_ENTITIES`. A section with nothing in it is DROPPED, so
        an empty dict means nothing was decodable and the caller should leave
        the previous state alone instead of publishing blanks.
    """
    result: dict[str, dict[str, Any]] = {"states": {}, "consumables": {}}

    if isinstance(content, dict):
        for section in ("states", "consumables"):
            if isinstance(content.get(section), dict):
                result[section].update(content[section])
        for key in ("powerStatus", "runningStatus", "warningWaterMissing", "warningFilter", "mode"):
            if key in content:
                result["states"][key] = content[key]
        if "filterPercentage" in content:
            result["consumables"]["filterPercentage"] = content["filterPercentage"]

    for cmd, data in _iter_ble_frames(content):
        if not data:
            continue
        if cmd in (CMD_DEVICE_STATUS, CMD_GET_STATE):
            dec = _decode_w5_status(data)
            result["states"].update(dec["states"])
            result["consumables"].update(dec["consumables"])
        elif cmd == CMD_GET_CONFIG:
            result["states"].update(_decode_w5_config(data))

    return {k: v for k, v in result.items() if v}


def w5_mode_payload(mode: int) -> bytes:
    """cmd 220 on a W5/W4/CTW2 — `[mode, submode]`.

    One byte carries power and mode together: 0 off, 1 normal, 2 smart. There
    is no separate power field, so switching the fountain off is mode 0 and
    switching it on is whichever mode it was last in.
    """
    return bytes([mode & 0xFF, 0x00])


def w5_config_payload(state: dict[str, Any]) -> bytes | None:
    """cmd 221 on a W5/W4/CTW2 — the 14-byte settings block.

    Returns None until a cmd-211 has been decoded for this accessory. Both
    schedules live in this block as minutes from midnight, and writing it from
    defaults would erase them; nothing else in the relayed traffic carries
    them, so there is nothing to reconstruct them from.
    """
    if any(k not in state for k in _W5_CONFIG_BYTE_FIELDS):
        return None
    if any(k not in state for k in _W5_CONFIG_INT_FIELDS):
        return None
    return bytes([
        int(state["smartWorkingTime"]) & 0xFF,
        int(state["smartSleepTime"]) & 0xFF,
        int(state["lampRingSwitch"]) & 0xFF,
        int(state["lampRingBrightness"]) & 0xFF,
        *int(state["lampRingLightUpTime"]).to_bytes(2, "big"),
        *int(state["lampRingGoOutTime"]).to_bytes(2, "big"),
        int(state["noDisturbingSwitch"]) & 0xFF,
        *int(state["noDisturbingStartTime"]).to_bytes(2, "big"),
        *int(state["noDisturbingEndTime"]).to_bytes(2, "big"),
        int(state.get("isLock", 0)) & 0xFF,
    ])


#: Entity key -> the field of `states` it sets, per frame. A block restates
#: every field it carries, so which frame a key belongs to decides what else
#: has to be read back out of the last status.
_W5_CONFIG_ENTITY_FIELDS = {
    "w5_light": "lampRingSwitch",
    "w5_brightness": "lampRingBrightness",
    "w5_dnd": "noDisturbingSwitch",
    "w5_child_lock": "isLock",
    "w5_smart_work": "smartWorkingTime",
    "w5_smart_sleep": "smartSleepTime",
}

W5_WRITABLE = (frozenset(_W5_CONFIG_ENTITY_FIELDS)
               | {"w5_power", "w5_mode", "w5_reset_filter"})


def _w5_command_for(ble_dev: BLEDevice, key: str, value: int) -> tuple[int, bytes]:
    """`(cmd, payload)` for one W5/W4/CTW2 entity. See `ble_command_for`."""
    states = dict(ble_dev.state.get("states") or {})

    if key in ("w5_power", "w5_mode"):
        if key == "w5_mode":
            mode = value
        elif value:
            # One byte is both power and mode, so switching on means naming a
            # mode — and the one to name is whichever it was last really in.
            # The decoder never stores a 0 (see `_decode_w5_status`), so this
            # reads the latched mode; normal is the fallback for a fountain
            # that has never reported one at all.
            mode = states.get("mode") or 1
        else:
            mode = 0
        return CMD_SET_MODE, w5_mode_payload(mode)

    states[_W5_CONFIG_ENTITY_FIELDS[key]] = value
    payload = w5_config_payload(states)
    if payload is None:
        raise Refused("no settings block reported yet - it is written whole, "
                      "and it carries both schedules")
    return CMD_SET_CONFIG, payload
