"""Crash-safe JSON/file persistence helpers.

Every state file in this add-on (`devices.json`, `ble_devices.json`,
`retention.json`, the panel's settings overrides, the bucket AES key) is written
through here, because a truncated one is indistinguishable from a missing one:
`DeviceRegistry._load` swallows the `JSONDecodeError` and starts empty, so every
device re-signs-up and is issued fresh MQTT credentials. Writing to a temporary
file and renaming it into place is what makes a container kill mid-write leave
the previous contents rather than half of the new ones.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Replace `path` with `data`, atomically as far as any reader can tell.

    Raises:
        OSError: if the directory cannot be created or the data cannot be
            written. The target file is left untouched in that case.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # The temp file must live in the target's directory: os.replace is only
    # atomic within a single filesystem, and /tmp is often a different one.
    # The leading dot keeps a stray temp file out of HA's media source listings
    # and out of any "*.json" glob.
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            # fsync before the rename, not after: os.replace only guarantees the
            # directory entry is swapped atomically, never that the file's
            # contents already reached the disk. Without this the kernel may
            # commit the rename first and a crash then leaves the target name
            # pointing at a zero-length file -- the exact corruption this module
            # exists to prevent.
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        # os.replace consumes the temp file only on success, so on any failure
        # (including a KeyboardInterrupt mid-write) it is still there.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_bytes(path: str | os.PathLike[str]) -> bytes:
    """Read a whole file into memory (blocking — call via a thread).

    The callers read a TLS certificate and a multi-megabyte media clip, both on
    the one asyncio loop the whole add-on shares, so neither may touch the file
    directly.

    Raises:
        OSError: unhandled on purpose — unlike `read_json` there is no sensible
            default for "the bytes you asked for", and both callers already
            treat a failed read as a failed operation.
    """
    with open(path, "rb") as f:
        return f.read()


def atomic_write_text(path: str | os.PathLike[str], text: str, *, encoding: str = "utf-8") -> None:
    """Replace `path` with `text`, atomically. See `atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: str | os.PathLike[str], obj: Any, *, indent: int | None = 2) -> None:
    """Serialise `obj` to JSON and replace `path` with it, atomically.

    Args:
        indent: passed through to `json.dumps`; None writes the compact form.

    Raises:
        TypeError: if `obj` is not JSON-serialisable. Serialisation happens
            before any file is touched, so the existing target survives intact.
        OSError: if the write fails; the target is likewise untouched.
    """
    atomic_write_text(path, json.dumps(obj, indent=indent))


def read_json(path: str | os.PathLike[str], default: Any) -> Any:
    """Load JSON from `path`, falling back to `default`.

    This deliberately degrades instead of raising: a missing file is the normal
    first-boot case, and a corrupt one must not stop the add-on from starting.
    Callers that need to tell "absent" from "damaged" apart should read the file
    themselves -- only the damaged case is logged here.

    Returns:
        The decoded JSON, or `default` if the file is missing, unreadable or not
        valid JSON.
    """
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as e:
        log.warning("could not read JSON from %s, using default: %s", path, e)
        return default
