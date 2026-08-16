"""Credential generation for the Aliyun IoT identity we hand each device.

The real PetKit cloud issues every device an Aliyun IoT triple (product key,
device name, device secret). We are that cloud here, so we mint the values
ourselves — they only have to have the SHAPE the firmware and our own MQTT auth
plugin expect, since both ends of the check are ours (`mqtt/auth.py` verifies a
signature computed with the very secret we handed out).

They are still generated with `secrets`, not `random`: the secret is the only
thing standing between a LAN neighbour and a device's MQTT session, and the
values are persisted to `devices.json` for the life of the device, so a
predictable one cannot be rotated away later.
"""
from __future__ import annotations

import secrets


def generate_device_secret() -> str:
    """A device secret: 32 lowercase hex characters.

    The value is opaque to everything that handles it — the device stores it and
    signs its MQTT connect with it, and `mqtt/auth.py` re-computes that
    signature from the same string — so only its unpredictability matters.
    """
    return secrets.token_hex(16)


def generate_product_key() -> str:
    """An Aliyun-shaped product key: the literal "a1" plus 8 hex characters.

    Aliyun product keys start with "a1", so ours does too. It ends up in the
    MQTT client id (`{pk}.{dn}|...`) and in every device topic
    (`/sys/{pk}/{dn}/...`), but is only ever compared against the copy we handed
    the device, so the value itself is arbitrary.
    """
    # token_hex(5) is 10 characters; the trailing pair is dropped to leave 8.
    return "a1" + secrets.token_hex(5)[:8]
