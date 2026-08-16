"""Rendering an untrusted payload into a log line, without raising and without
letting it set its own length.

Both properties are load-bearing. A frame reaches a log precisely when it was
unexpected, so it may be binary, truncated or not UTF-8 at all — decoding it
strictly would turn a diagnostic into a second failure. And an MQTT payload or a
retained Home Assistant command is caller-controlled and arbitrarily large, so
whatever renders it has to impose the bound rather than inherit one.

The bound itself is NOT fixed here: it belongs to the traffic being logged, and
the callers' two are an order apart (an HA command payload against a device
frame carrying a media notification). Each caller passes its own.
"""
from __future__ import annotations


def payload_text(payload: object) -> str:
    """Decode a raw payload to text, never raising on a binary one."""
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload).decode("utf-8", errors="replace")
    return str(payload)


def excerpt(payload: object, limit: int) -> str:
    """`payload_text`, capped at `limit` characters and marked where it was cut."""
    text = payload_text(payload)
    return (text[:limit] + "...") if len(text) > limit else text
