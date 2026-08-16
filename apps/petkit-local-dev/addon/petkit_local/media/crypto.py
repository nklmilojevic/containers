"""AES decryption for device media uploads, plus magic-byte auto-detection of
whether a file actually needs decrypting.

The device gets a 16-character key string via dev_oss_sts_info_new_v2
(primaryAesKeyStr) and encrypts each uploaded file with it — see
dev_upload_file_info_v2's `encrypt`/`aesIv` fields. Reverse-engineered
assumption (NOT yet confirmed against a real file — see the media/events
plan's Verification section, checked against magic bytes at decrypt time so a
wrong guess degrades to "keep the raw file" instead of corrupting it):
AES-128-CBC, key = the 16 ASCII bytes of the key string itself (not
hex-decoded — the string IS the key material, which is why it's generated as
a hex *string* in the first place: 8 random bytes -> 16 hex chars -> a valid
AES-128 key length), IV = bytes.fromhex(aesIv with its "0x" prefix stripped).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from petkit_local.utils.jsonio import atomic_write_text

log = logging.getLogger(__name__)

_KEY_FILENAME = "bucket_aes_key.txt"


def resolve_key(config: Any) -> bytes:
    """The stable AES-128 key for bucket uploads, as raw key bytes (the 16
    ASCII bytes of the key string — see module docstring). Generated once and
    persisted (the same file dev_oss_sts_info_new_v2 hands out), so encrypted
    files can always be decrypted later.

    A key that is already on disk is returned verbatim even if it looks odd:
    rotating it would make every file the device has already uploaded
    permanently undecryptable, which is strictly worse than surfacing a bad
    key downstream. The one exception is an empty file — that holds no key to
    lose, and is what a crash during the pre-atomic-write era left behind.
    """
    data_dir = config.get("data_dir", "/data") if isinstance(config, dict) else "/data"
    key_path = os.path.join(data_dir, _KEY_FILENAME)

    key_str = ""
    try:
        with open(key_path, encoding="ascii") as f:
            key_str = f.read().strip()
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        # Unreadable is not the same as absent: generating a replacement here
        # would silently orphan every encrypted upload made so far.
        raise RuntimeError(f"bucket AES key at {key_path} is unreadable: {e}") from e

    if not key_str:
        if os.path.exists(key_path):
            log.warning("Bucket AES key at %s was empty - generating a new one; "
                        "any already-uploaded encrypted file cannot be decrypted", key_path)
        key_str = os.urandom(8).hex()
        # Atomic: a torn write here loses the key for every file the device has
        # already encrypted with it.
        atomic_write_text(key_path, key_str)
    return key_str.encode("ascii")


def resolve_key_string(config: Any) -> str:
    """The same key as `resolve_key`, as the string handed to the device in
    dev_oss_sts_info_new_v2 (primaryAesKeyStr)."""
    return resolve_key(config).decode("ascii")


# --- magic-byte detection ---------------------------------------------

def _looks_like_jpeg(head: bytes) -> bool:
    """SOI marker plus the first byte of any APPn/DQT segment."""
    return head[:3] == b"\xff\xd8\xff"


def _looks_like_png(head: bytes) -> bool:
    """Full 8-byte PNG signature, or its 4-byte prefix for a truncated head."""
    return head[:8] == b"\x89PNG\r\n\x1a\n" or head[:4] == b"\x89PNG"


def _looks_like_mpegts(head: bytes) -> bool:
    """Two sync bytes one packet apart — a single 0x47 is far too weak.

    MPEG-TS has no file signature at all: it is a stream of fixed 188-byte
    packets each starting with 0x47, so the packet *pitch* is the only real
    evidence. Only a head shorter than two packets falls back to the
    single-byte check.
    """
    if len(head) < 189:
        return len(head) >= 1 and head[0] == 0x47
    return head[0] == 0x47 and head[188] == 0x47


def looks_plaintext(head: bytes) -> bool:
    """True if `head` (a file's first ~400 bytes) already looks like a real
    JPEG/PNG/MPEG-TS — i.e. decryption is unnecessary. Checked instead of
    trusting the file_info `encrypt` flag so the pipeline is robust either
    way (firmware not actually encrypting, or a wrong key/IV assumption)."""
    if not head:
        return False
    return _looks_like_jpeg(head) or _looks_like_png(head) or _looks_like_mpegts(head)


def decrypt_aes(data: bytes, key: bytes, iv_hex: str) -> bytes:
    """AES-128-CBC decrypt. Tolerant of a non-padded/truncated tail (the
    device may not PKCS7-pad a live-streamed clip) — only full 16-byte blocks
    are decrypted; any partial trailing block is appended unmodified."""
    # `cryptography` is optional everywhere else it is used here (mqtt/broker.py
    # starts without a TLS listener when it is absent), so importing it at module
    # scope would take the whole device-facing stub table down with it.
    from cryptography.hazmat.primitives.ciphers import (  # noqa: PLC0415
        Cipher, algorithms, modes,
    )

    iv_hex = (iv_hex or "").strip()
    if iv_hex[:2].lower() == "0x":
        iv_hex = iv_hex[2:]
    try:
        iv = bytes.fromhex(iv_hex)
    except ValueError:
        raise ValueError(f"bad aesIv: {iv_hex!r}")
    if len(iv) != 16:
        raise ValueError(f"aesIv must decode to 16 bytes, got {len(iv)}")
    if len(key) != 16:
        raise ValueError(f"AES-128 key must be 16 bytes, got {len(key)}")

    block_count = len(data) // 16
    body, tail = data[:block_count * 16], data[block_count * 16:]

    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    return plain + tail
