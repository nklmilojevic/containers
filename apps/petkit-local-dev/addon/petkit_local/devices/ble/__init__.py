"""BLE accessory devices: the Pura Air spray and the EverSweet fountains.

None of these has a network of its own (`utils/const.py::DEVICE_TYPES_BLE_ONLY`).
Each pairs over BLE to a mains-powered WiFi device — a litter box or a feeder —
which relays for it, so everything here arrives inside that parent's traffic:
K3 readings ride along in its `property/post`, and the fountains' arrive as
`ble_response/post` frames carrying a binary protocol this package decodes.

Pairing is ours to hold because the parent does not discover anything: it asks
the cloud for a list of MACs and scans for exactly those, and no firmware can
report a new one upward.

The split is by what differs: `framing.py` is the frame both fountain families
share, `w5.py` and `ctw3.py` are the DATA layout inside it that they do not,
and `registry.py` is the accessories themselves and the store holding them.
What is left here is what has to choose between the two families — and the one
import path every caller uses, which is why everything is re-exported.
"""
from __future__ import annotations

from petkit_local.devices.base import Refused
from petkit_local.devices.ble.ctw3 import (
    _CTW3_BYTE_FIELDS, _CTW3_CONFIG_FIELDS, _CTW3_INT_FIELDS, _CTW3_MIN_STATUS_LEN,
    _CTW3_MODE_FIELDS, _ctw3_command_for, _decode_ctw3_status, CTW3_CONFIG_LEN,
    CTW3_CONFIG_OFFSET, CTW3_WRITABLE, ctw3_config_payload, ctw3_mode_payload,
    decode_ctw3_config, parse_ctw3_ble_response,
)
from petkit_local.devices.ble.framing import (
    _ble_decode_data, _ble_unframe, _iter_ble_frames, BLE_FRAME_HEADER,
    BLE_FRAME_HEADER_LEN, BLE_FRAME_TRAILER, BLE_SENDABLE, BLE_TYPE_REQUEST,
    CMD_DEVICE_STATUS, CMD_GET_CONFIG, CMD_GET_STATE, CMD_RESET_FILTER,
    CMD_SET_CONFIG, CMD_SET_MODE, FOUNTAIN_MODES, ble_command_frame, build_ble_frame,
)
from petkit_local.devices.ble.registry import (
    _cloud_binding, BLE_TYPE_CONFIRMED, BLE_TYPE_MAP, BLE_TYPES, BLEDevice,
    BLERegistry, CLOUD_BINDING_ENDPOINTS, CLOUD_BLE_TYPES, K3_DEFAULT_CONFIG,
    K3_SETTING_KEYS, cloud_bindings, normalize_mac,
)
from petkit_local.devices.ble.w5 import (
    _W5_CONFIG_BYTE_FIELDS, _W5_CONFIG_ENTITY_FIELDS, _W5_CONFIG_INT_FIELDS,
    _W5_MIN_STATUS_LEN, _W5_STATUS_FILTER_OFFSET, _W5_STATUS_STATE_OFFSETS,
    _decode_w5_config, _decode_w5_status, _w5_command_for, W5_CONFIG_LEN,
    W5_CONFIG_LOCK_OFFSET, W5_WRITABLE, parse_w5_ble_response, w5_config_payload,
    w5_mode_payload,
)

#: The EverSweets that speak the W5 BLE protocol.
#:
#: One pair of GATT UUIDs and one frame parser serve all of them in
#: `phldgmn/ha-petkit-ble` and in `aavdberg/ha-petkit`, both of which advertise
#: for `Petkit_W5`, `Petkit_W5C`, `Petkit_W5N`, `Petkit_W4X`, `Petkit_W4XUVC`
#: and `Petkit_CTW2` — so a frame from any of them decodes with
#: `parse_w5_ble_response` and reads through `W5_ENTITIES`. The variants are
#: not separate `ble_type`s here for that reason: there is nothing to branch
#: on, and each new one would need a `BLE_TYPE_MAP` scan number nobody has.
#:
#: CTW3 does NOT belong here — its status block is a different length with a
#: different layout, so reading it with these offsets would produce confident
#: nonsense, and `aavdberg/ha-petkit` keeps it on a separate parser for the
#: same reason. It has its own decoder further down.
W5_PROTOCOL = frozenset({"w5", "w4", "ctw2"})

#: The buttons, and the command each one is. No payload is built from state —
#: they carry none.
_RESET_FILTER_KEYS = {"ctw3_reset_filter", "w5_reset_filter"}

#: Empty. mr-ransel's notes say so and PetKit's app agrees — its
#: `getCTW3ResetFilterElement` is `PetkitBleMsg(222, new byte[0])`.
#: aavdberg/ha-petkit sends a single zero; 1.6.1 copied that.
_RESET_FILTER_PAYLOAD = b""


def ble_command_for(ble_dev: BLEDevice, key: str, value: int) -> tuple[int, bytes]:
    """The `(cmd, payload)` that sets one accessory entity to `value`.

    No frame carries a single field, so each is built from the accessory's last
    decoded status with one value replaced.

    Raises:
        Refused: when the key is not writable on this accessory, or when the
            block it belongs to has never been reported in full. Both mean the
            same thing from Home Assistant — the control snaps back — so both
            say which it was.
    """
    if key in _RESET_FILTER_KEYS:
        return CMD_RESET_FILTER, _RESET_FILTER_PAYLOAD
    if ble_dev.ble_type == "ctw3" and key in CTW3_WRITABLE:
        return _ctw3_command_for(ble_dev, key, value)
    if ble_dev.ble_type in W5_PROTOCOL and key in W5_WRITABLE:
        return _w5_command_for(ble_dev, key, value)
    raise Refused(f"{key} is not a writable {ble_dev.ble_type.upper()} field")


#: Which decoder an accessory's frames go through.
BLE_PARSERS = {
    "ctw3": parse_ctw3_ble_response,
}


def parser_for(ble_type: str):
    """The frame decoder for an accessory kind, or None if it has none."""
    if ble_type in W5_PROTOCOL:
        return parse_w5_ble_response
    return BLE_PARSERS.get(ble_type)


# Every name this module held before it became a package, private ones
# included: `from petkit_local.devices.ble import X` is the import path used
# across the add-on and its tests, and a split is not a reason to break one.
__all__ = [
    "BLEDevice",
    "BLERegistry",
    "BLE_FRAME_HEADER",
    "BLE_FRAME_HEADER_LEN",
    "BLE_FRAME_TRAILER",
    "BLE_PARSERS",
    "BLE_SENDABLE",
    "BLE_TYPES",
    "BLE_TYPE_CONFIRMED",
    "BLE_TYPE_MAP",
    "BLE_TYPE_REQUEST",
    "CLOUD_BINDING_ENDPOINTS",
    "CLOUD_BLE_TYPES",
    "CMD_DEVICE_STATUS",
    "CMD_GET_CONFIG",
    "CMD_GET_STATE",
    "CMD_RESET_FILTER",
    "CMD_SET_CONFIG",
    "CMD_SET_MODE",
    "CTW3_CONFIG_LEN",
    "CTW3_CONFIG_OFFSET",
    "CTW3_WRITABLE",
    "FOUNTAIN_MODES",
    "K3_DEFAULT_CONFIG",
    "K3_SETTING_KEYS",
    "Refused",
    "W5_CONFIG_LEN",
    "W5_CONFIG_LOCK_OFFSET",
    "W5_PROTOCOL",
    "W5_WRITABLE",
    "_CTW3_BYTE_FIELDS",
    "_CTW3_CONFIG_FIELDS",
    "_CTW3_INT_FIELDS",
    "_CTW3_MIN_STATUS_LEN",
    "_CTW3_MODE_FIELDS",
    "_RESET_FILTER_KEYS",
    "_RESET_FILTER_PAYLOAD",
    "_W5_CONFIG_BYTE_FIELDS",
    "_W5_CONFIG_ENTITY_FIELDS",
    "_W5_CONFIG_INT_FIELDS",
    "_W5_MIN_STATUS_LEN",
    "_W5_STATUS_FILTER_OFFSET",
    "_W5_STATUS_STATE_OFFSETS",
    "_ble_decode_data",
    "_ble_unframe",
    "_cloud_binding",
    "_ctw3_command_for",
    "_decode_ctw3_status",
    "_decode_w5_config",
    "_decode_w5_status",
    "_iter_ble_frames",
    "_w5_command_for",
    "ble_command_for",
    "ble_command_frame",
    "build_ble_frame",
    "cloud_bindings",
    "ctw3_config_payload",
    "ctw3_mode_payload",
    "decode_ctw3_config",
    "normalize_mac",
    "parse_ctw3_ble_response",
    "parse_w5_ble_response",
    "parser_for",
    "w5_config_payload",
    "w5_mode_payload",
]
