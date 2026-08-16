"""What a CTW3 frame's DATA block means, in both directions.

The EverSweet Max Cordless speaks the framing in `framing.py` and nothing else
in common with the W5 family: different block lengths, different offsets,
different field names. Its decoder and its payload builders are therefore its
own, and reading one family's block with the other's offsets is the mistake the
separation exists to prevent (`w5.py` holds the other half).
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

# --- CTW3 (EverSweet Max Cordless) ------------------------------------------
#
# A different DATA layout in the same framing, contributed with the hardware in
# hand (issue #4, firmware 111). Do NOT read it with the W5 offsets: the blocks
# are 30 bytes (cmd 210) or 42 (cmd 230, with a 12-byte config tail), against
# the W5's much shorter one, and the multi-byte values here are big-endian.
#
# The names are PetKit's own, taken from the account API's `kv` for this model
# rather than translated into the W5's vocabulary — `runStatus` not
# `runningStatus`, `lackWarning` not `warningWaterMissing`. Where the two
# families disagree, matching the cloud is what lets a report be compared with
# what the PetKit app shows.

#: Everything the decoder names fits in 26 bytes, and 26 is what an older
#: firmware sends — aavdberg/ha-petkit accepts that length from real hardware.
#: Demanding more drops such a block whole rather than reading the part of it
#: this decoder understands.
_CTW3_MIN_STATUS_LEN = 26

#: Single-byte fields, offset into the DATA block.
_CTW3_BYTE_FIELDS = {
    "powerStatus": 0,
    # Inverted against the English: 0 is paused/sleeping, 1 is working.
    "suspendStatus": 1,
    "mode": 2,                  # 1 normal (continuous), 2 smart (cycling)
    # NOT a boolean. 2 is the AC path — the fountain is on mains.
    "electricStatus": 3,
    "noDisturbingSwitch": 4,
    "breakdownWarning": 5,
    "lackWarning": 6,
    "lowBattery": 7,
    "filterWarning": 8,
    "filterPercent": 13,
    "runStatus": 14,
    # 0 is nobody; anything else (0x02 observed) is a pet at the bowl.
    "detectStatus": 19,
    "batteryPercent": 24,
    # Undecoded. Kept because it is the last byte of the short block and a
    # value nobody can see is a value nobody can explain.
    "moduleStatus": 25,
}

#: Multi-byte fields as `(offset, width)`, big-endian.
_CTW3_INT_FIELDS = {
    "waterPumpRunTime": (9, 4),
    "todayPumpRunTime": (15, 4),
    "supplyVoltage": (20, 2),
    "batteryVoltage": (22, 2),
}

#: Where the 12-byte config tail starts in a cmd-230 block.
CTW3_CONFIG_OFFSET = 30
CTW3_CONFIG_LEN = 12


def decode_ctw3_config(tail: bytes) -> dict[str, int]:
    """The 12-byte config tail of a cmd-230 status, as named fields.

    Everything a cmd-221 write has to restate comes from here, which is what
    makes changing one setting possible without inventing the rest.

    SETTLED, and it was briefly unsettled for no good reason.

    PetKit's own app builds this exact block for a cmd-221 write, field by
    field (`CTW3DataConvertor.changeSmartMode` / `changeBatteryMode`, app
    13.8.1): smart working time, smart sleep time, battery working time and
    battery sleep time as big-endian shorts, then lamp ring switch, lamp ring
    brightness, do-not-disturb, child lock, and two inductive switches. Twelve
    bytes, in the order below.

    So the status tail and the write payload ARE one layout, which is what the
    real cmd-230 frame from issue #4 already said — 1, 3, 0 at bytes 6, 7, 8 is
    a fountain with the ring on at high and quiet hours off.
    aavdberg/ha-petkit reads those three as do-not-disturb / light / brightness
    and writes ten bytes rather than twelve; 1.6.0 followed them for the write
    and was wrong to. The app is the other end of that conversation and does
    not need interpreting.
    """
    if len(tail) < CTW3_CONFIG_LEN:
        return {}
    return {
        "smartWorkingTime": tail[0],
        "smartSleepTime": tail[1],
        "energyInterval": int.from_bytes(tail[2:4], "big"),
        "sleepTime": int.from_bytes(tail[4:6], "big"),
        "lightSwitch": tail[6],
        "brightness": tail[7],          # 1 low, 2 medium, 3 high
        "noDisturbingSwitch": tail[8],
        # Written by the app only on hardware+firmware/100 >= 1.35
        # (`CTW3Utils.isSupportLockVersion`), and left at 0 below it.
        "childLock": tail[9],
        "smartInductiveSwitch": tail[10],
        "batteryInductiveSwitch": tail[11],
    }


def _decode_ctw3_status(data: bytes) -> dict[str, dict[str, int]]:
    """Decode a CTW3 cmd-210/230 DATA block.

    Refuses anything shorter than the 26 bytes the short form is defined to be,
    rather than emitting the handful of fields that happen to fit — a one-byte
    ACK read permissively becomes a confident `powerStatus`. The block length is
    known, so a short one is a broken frame and is dropped.
    """
    if len(data) < _CTW3_MIN_STATUS_LEN:
        log.warning("CTW3 status frame is %d bytes, expected at least %d - dropped",
                    len(data), _CTW3_MIN_STATUS_LEN)
        return {"states": {}, "consumables": {}}

    states: dict[str, int] = {name: data[off] for name, off in _CTW3_BYTE_FIELDS.items()}
    for name, (off, width) in _CTW3_INT_FIELDS.items():
        states[name] = int.from_bytes(data[off:off + width], "big")

    # A mode outside 1/2 is the smart cycle's sleep half, not a new mode. Drop
    # the key rather than store it: the caller merges, so absence keeps what was
    # there, and what was there is the last mode the fountain really had.
    if states.get("mode") not in FOUNTAIN_MODES:
        log.debug("CTW3 reported mode=%s - keeping the last real one",
                  states.get("mode"))
        states.pop("mode", None)

    # `filterPercent` is the one field a human treats as a consumable rather
    # than a state, and the entity reads it from there.
    consumables = {"filterPercent": states.pop("filterPercent")}

    if len(data) >= CTW3_CONFIG_OFFSET + CTW3_CONFIG_LEN:
        tail = data[CTW3_CONFIG_OFFSET:CTW3_CONFIG_OFFSET + CTW3_CONFIG_LEN]
        # `noDisturbingSwitch` appears in both halves; the tail is the one a
        # config write round-trips, so let it win.
        states.update(decode_ctw3_config(tail))

    return {"states": states, "consumables": consumables}


def parse_ctw3_ble_response(content: Any) -> dict[str, dict[str, Any]]:
    """Decode a CTW3 `ble_response` into the state its entities read.

    Same contract as `parse_w5_ble_response`: an empty dict means nothing was
    decodable, and the caller leaves the previous state alone.

    cmd 211 is the settings block, as on the W5 family, and a CTW3 does answer
    it over the relay — see the capture in the branch below. Its settings also
    arrive as the tail of a long cmd-230, so both paths decode the same block.
    """
    result: dict[str, dict[str, Any]] = {"states": {}, "consumables": {}}
    for cmd, data in _iter_ble_frames(content):
        if not data:
            continue
        if cmd in (CMD_DEVICE_STATUS, CMD_GET_STATE):
            dec = _decode_ctw3_status(data)
            result["states"].update(dec["states"])
            result["consumables"].update(dec["consumables"])
        elif cmd == CMD_GET_CONFIG:
            # A CTW3 DOES answer 211 — over the relay, at least. It is silent
            # to a direct GATT client on firmware 111, which is why
            # aavdberg/ha-petkit skips the command entirely, and why nothing
            # here ever asked. The reply is the same 12-byte block as a 221
            # write and the tail of a long 230, so it decodes with no new
            # offsets: `03 03 00 78 04 b0 01 03 00 00 01 00` off a live one.
            result["states"].update(decode_ctw3_config(data))
    return {k: v for k, v in result.items() if v}


def ctw3_mode_payload(power: int, suspend: int, mode: int) -> bytes:
    """cmd 220 on a CTW3 — the three settings that share one frame.

    All three travel together, so changing one means restating the other two.
    `suspend` is 1 for WORKING and 0 for paused, inverted against its name; a
    fountain being switched off has nothing to suspend, so 0 is forced there
    rather than left at whatever the last reading held.

    Three bytes, matching aavdberg/ha-petkit's `build_ctw3_mode_payload`. A
    leading zero does not belong here: that byte is the frame header's `len_hi`,
    and putting it in the payload compensates for a framing bug in the wrong
    place (see `framing.py` on the 16-bit length).
    """
    if not power:
        suspend = 0
    return bytes([power & 0xFF, suspend & 0xFF, mode & 0xFF])


def ctw3_config_payload(state: dict[str, Any]) -> bytes | None:
    """cmd 221 on a CTW3 — the settings block, rebuilt from the last status.

    Twelve bytes, the same layout `decode_ctw3_config` reads and the same one
    PetKit's app writes. A third-party capture shows ten bytes in a different
    order; where the two disagree the app's own write is what the accessory is
    known to accept.

    Returns None when the accessory has never reported a long status.
    """
    needed = ("smartWorkingTime", "smartSleepTime", "energyInterval", "sleepTime",
              "lightSwitch", "brightness", "noDisturbingSwitch")
    if any(k not in state for k in needed):
        return None
    return bytes([
        int(state["smartWorkingTime"]) & 0xFF,
        int(state["smartSleepTime"]) & 0xFF,
        *int(state["energyInterval"]).to_bytes(2, "big"),
        *int(state["sleepTime"]).to_bytes(2, "big"),
        int(state["lightSwitch"]) & 0xFF,
        int(state["brightness"]) & 0xFF,
        int(state["noDisturbingSwitch"]) & 0xFF,
        int(state.get("childLock", 0)) & 0xFF,
        int(state.get("smartInductiveSwitch", 0)) & 0xFF,
        int(state.get("batteryInductiveSwitch", 0)) & 0xFF,
    ])


#: Entity key -> the field of `states` it sets, per frame. A block restates
#: every field it carries, so which frame a key belongs to decides what else
#: has to be read back out of the last status.
_CTW3_MODE_FIELDS = {
    "ctw3_power": "powerStatus",
    "ctw3_working": "suspendStatus",
    "ctw3_mode": "mode",
}
_CTW3_CONFIG_FIELDS = {
    "ctw3_light": "lightSwitch",
    "ctw3_brightness": "brightness",
    "ctw3_dnd": "noDisturbingSwitch",
    "ctw3_child_lock": "childLock",
    "ctw3_energy_interval": "energyInterval",
    "ctw3_sleep_time": "sleepTime",
    "ctw3_smart_work": "smartWorkingTime",
    "ctw3_smart_sleep": "smartSleepTime",
}

CTW3_WRITABLE = (frozenset(_CTW3_MODE_FIELDS) | frozenset(_CTW3_CONFIG_FIELDS)
                 | {"ctw3_reset_filter"})


def _ctw3_command_for(ble_dev: BLEDevice, key: str, value: int) -> tuple[int, bytes]:
    """`(cmd, payload)` for one CTW3 entity. See `ble_command_for`."""
    states = dict(ble_dev.state.get("states") or {})

    if key in _CTW3_MODE_FIELDS:
        states[_CTW3_MODE_FIELDS[key]] = value
        if key == "ctw3_mode":
            # Picking a mode means "run in it": PetKit's app sends power 1 and
            # pause 1 with the chosen mode and reads nothing back
            # (`CTW3HomePresenter.changeDeviceMode`, app 13.8.1). Taking power
            # from the last status instead sends 0 whenever the fountain was
            # caught in the sleep half of its smart cycle, leaving the pump off
            # and the select looking like it did nothing (aavdberg/ha-petkit
            # issue #54) — they fixed the power half and derived pause from the
            # mode, which the app does not do.
            states["powerStatus"] = 1
            states["suspendStatus"] = 1
        missing = [f for f in ("powerStatus", "suspendStatus", "mode") if f not in states]
        if missing:
            raise Refused(f"no reading yet for {', '.join(missing)}")
        return CMD_SET_MODE, ctw3_mode_payload(
            states["powerStatus"], states["suspendStatus"], states["mode"])

    states[_CTW3_CONFIG_FIELDS[key]] = value
    payload = ctw3_config_payload(states)
    if payload is None:
        raise Refused("no full status reported yet - the settings block is "
                      "written whole, and the rest of it is not known")
    return CMD_SET_CONFIG, payload
