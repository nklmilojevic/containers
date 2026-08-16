"""The frame layer both fountain families share, in both directions.

One header, one command numbering, one encoding — and the handful of protocol
facts that are not per-model either. The DATA layout inside a frame is the part
that differs, and it lives in `w5.py` and `ctw3.py`; nothing here may import
them, because both import this.
"""
from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Iterator
from typing import Any

# The framing both fountain families share, carried as urlencode(base64(...))
# inside a relayed `ble_response`:
#
#     FA FC FD | cmd | type | seq | len_lo | len_hi | payload | FB
#
# Only the DATA layout inside it differs per model, so the decode/unframe
# helpers below are shared and the offset tables are not.
#
# The length is a 16-bit LITTLE-endian field, and getting that wrong is not a
# cosmetic detail: emit a single length byte and the accessory reads the first
# byte of our PAYLOAD as the high half of the length. A 4-byte mode write then
# announces 4 bytes and delivers 3 plus the trailer; a 12-byte config write
# announces 0x030C = 780 and is still being waited for when the session closes.
# Both fail in silence.
#
# Three sources agree on the 8-byte header: mr-ransel's W5 protocol notes,
# aavdberg/ha-petkit's builder and parser, and `_ble_unframe` immediately
# below, which reads the payload from offset 8.
BLE_FRAME_HEADER = bytes([0xFA, 0xFC, 0xFD])
BLE_FRAME_TRAILER = 0xFB
BLE_FRAME_HEADER_LEN = 8

#: The `type` byte: 1 request, 2 response, 3 request wanting no answer. We only
#: ever send the first.
BLE_TYPE_REQUEST = 0x01

# The commands, in the one numbering that runs in both directions. `cmd` and
# the opcode inside the frame are THE SAME NUMBER — 220 is 0xDC — which is
# worth stating because mapping one to the other as though they were different
# numbers makes 222 look like a value nobody has.
CMD_GET_STATE = 210         # short status block, on request
CMD_GET_CONFIG = 211        # settings block. A CTW3 answers it over the relay.
CMD_SET_MODE = 220          # power / mode. CTW3 adds a suspend byte.
CMD_SET_CONFIG = 221        # the settings block, written whole
CMD_RESET_FILTER = 222      # filter life back to 100%
CMD_DEVICE_STATUS = 230     # status the accessory pushes unasked

#: The commands we know how to build a frame for. Anything else is refused
#: rather than sent as a frame whose length or payload we would be inventing.
BLE_SENDABLE = frozenset({CMD_SET_MODE, CMD_SET_CONFIG, CMD_RESET_FILTER})

#: The mode values that are real, on EVERY fountain. Anything else is the
#: device saying "not running right now" in a field that has no way to say it,
#: and it must not be stored — `ble_command_for` rebuilds the mode frame from
#: the last reading, so a latched 0 is a command to switch off.
#:
#: A CTW3 reports 0 in the sleep half of its smart cycle (aavdberg/ha-petkit
#: issue #57). A W5, W4, W4X or CTW2 reports 0 whenever it is powered off
#: (their #106), which is a different cause with the same two consequences: the
#: mode select renders blank, and switching the fountain back on silently puts
#: it in normal mode because the smart it was in reads as nothing.
FOUNTAIN_MODES = (1, 2)


def _ble_decode_data(blob: Any) -> bytes | None:
    """Decode one `ble_response` frame's `data` field into raw bytes.

    The field is urlencode(base64(bytes)) — localkit's W5/Device.php reads it as
    `base64_decode(urldecode(payload))`. Returns None for anything that is
    neither that nor the observed hex fallback, so a garbled frame is skipped
    rather than decoded into nonsense.
    """
    if isinstance(blob, (bytes, bytearray)):
        return bytes(blob)
    if not isinstance(blob, str):
        return None
    s = urllib.parse.unquote(blob).strip()
    try:
        return base64.b64decode(s)
    except Exception:
        # Not every payload is base64; the hex form is the observed fallback.
        try:
            return bytes.fromhex(s)
        except ValueError:
            return None


def _ble_unframe(raw: bytes) -> tuple[int | None, bytes]:
    """Split a W5 BLE frame into `(cmd, data_bytes)`.

    Handles both a full `FA FC FD` frame and the pre-split case where `data`
    already IS the DATA payload; in the latter case the command is None and the
    caller falls back to the `cmd` the JSON carried alongside it.
    """
    if len(raw) >= BLE_FRAME_HEADER_LEN + 1 and raw[:3] == BLE_FRAME_HEADER:
        return raw[3], raw[BLE_FRAME_HEADER_LEN:-1]
    return None, raw


def _iter_ble_frames(content: Any) -> Iterator[tuple[Any, bytes]]:
    """Yield `(cmd, data_bytes)` for every frame in a `ble_response` content.

    The content shape is `{device: {mac}, payload: [{cmd, data}, ...]}`; a
    single loose blob under `data`/`value`/`frame` is accepted too, because the
    proxying firmware does not always wrap one frame in a list. Undecodable
    entries are skipped silently — a BLE accessory is a best-effort extra, and
    one bad frame must not cost the parent's whole report.
    """
    if not isinstance(content, dict):
        return
    payload = content.get("payload")
    items = payload if isinstance(payload, list) else []
    # tolerate a single loose blob too
    for alt in ("data", "value", "frame"):
        if alt in content and content[alt] is not None:
            items = items + [{"cmd": None, "data": content[alt]}]
    for item in items:
        if not isinstance(item, dict) or "data" not in item:
            continue
        raw = _ble_decode_data(item.get("data"))
        if raw is None:
            continue
        framed_cmd, data = _ble_unframe(raw)
        yield (framed_cmd if framed_cmd is not None else item.get("cmd"), data)


# --- writing to an accessory -------------------------------------------------
#
# The other direction of the same relay: we publish `thing/service/ble` to the
# PARENT, which forwards the bytes over its open BLE session and does not
# interpret them.
#
# Every write here restates a whole block. The device has no "set one field"
# frame, so each builder starts from the accessory's last decoded status and
# replaces one value — and refuses outright when that status has never
# arrived. Filling the unknown half with zeros would turn the light off and
# reset both intervals as a side effect of changing the brightness.


def build_ble_frame(cmd: int, seq: int, payload: bytes) -> str:
    """One outbound BLE frame, encoded the way `thing/service/ble` carries it.

    `FA FC FD | cmd | type | seq | len_lo | len_hi | payload | FB`, then base64,
    then urlencode — the exact inverse of what `_ble_decode_data` and
    `_ble_unframe` undo on the way in.

    `seq` wraps at a byte. Nothing has been observed rejecting a repeat, but it
    is a sequence number and sending a constant would be the kind of detail
    that works until it does not.
    """
    body = bytes([*BLE_FRAME_HEADER, cmd & 0xFF, BLE_TYPE_REQUEST, seq & 0xFF,
                  len(payload) & 0xFF, (len(payload) >> 8) & 0xFF,
                  *payload, BLE_FRAME_TRAILER])
    return urllib.parse.quote(base64.b64encode(body).decode())


def ble_command_frame(cmd: int, seq: int, payload: bytes) -> str | None:
    """The encoded frame for one command, or None if it is not one we send."""
    if cmd not in BLE_SENDABLE:
        return None
    return build_ble_frame(cmd, seq, payload)
