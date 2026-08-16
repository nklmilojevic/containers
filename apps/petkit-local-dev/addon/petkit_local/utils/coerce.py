"""Never-raising scalar coercion: `to_int`, `to_float`, `to_bool`.

Two problems make this module worth having. First, "parse a scalar, fall back
instead of raising" had grown three private copies under three naming schemes
(`_coerce_switch`/`_coerce_number` in `ha/commands.py`, `_as_int`/`_to_float`
in `events/normalize.py`), each accepting a slightly different set of inputs, so
the same device value could parse in one code path and not in another. Second,
request handlers call bare `int()` on DEVICE-CONTROLLED input — a device that
sends a non-numeric `X-Device` id makes `int(x_dev.get("id", 0))` raise
`ValueError` and the request answer HTTP 500. Every function here takes an
explicit `default` and returns it instead of raising, so untrusted input can
degrade a value but never abort a request.

These functions are a superset of the four helpers they replace, with one
deliberate narrowing: underscore digit separators are rejected (see
`_INT_TEXT` below).
"""
from __future__ import annotations

import math
import re
from typing import Any, TypeVar

T = TypeVar("T")

# Deliberately stricter than int()/float(). Both builtins accept `_` digit
# separators (`int("1_0") == 10`) and non-ASCII digits (`int("١٢٣") == 123`).
# This codebase has already been bitten by the first one: `events/normalize.py`
# has to recognise the device's bare token `4_10000001_1784743819` as NOT a
# number, which `float()` alone gets wrong. Rejecting both here keeps one
# answer to "is this text a number?" across the codebase. Leading zeros ARE
# accepted ("007" -> 7) — zero-padded ids are plausible on the wire and
# unambiguous, unlike a separator.
_INT_TEXT = re.compile(r"[+-]?[0-9]+\Z")
# Same grammar for floats, and note what it excludes by construction: "inf",
# "-Infinity" and "nan", all of which `float()` happily accepts.
_FLOAT_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")

# HA publishes switch commands as the `payload_on`/`payload_off` strings from
# ha/discovery.py ("ON"/"OFF"); the device and our own templates use 0/1 and
# "true"/"false". Matching is case-insensitive after stripping.
_TRUE_TEXT = frozenset({"1", "true", "on", "yes"})
_FALSE_TEXT = frozenset({"0", "false", "off", "no"})


def to_int(value: Any, default: T) -> int | T:
    """Coerce `value` to an int, returning `default` if it is not one.

    Accepts int, bool (False/True -> 0/1), finite float, and a numeric string
    with surrounding whitespace — including a float-shaped one (" 1.9 " -> 1).
    Fractions are truncated TOWARD ZERO (-1.9 -> -1), matching `int()` rather
    than rounding, so a coerced value never crosses zero or jumps a threshold.

    Rejected (returns `default`): None, empty/blank string, any non-numeric
    text, underscore separators ("1_0"), non-ASCII digits, infinities and NaN
    in either float or string form, and any other type.
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, str):
        text = value.strip()
        if _INT_TEXT.match(text):
            try:
                return int(text)
            except ValueError:
                # CPython caps int(str) at sys.int_max_str_digits (4300 by
                # default) to bound quadratic parsing cost. A device is free
                # to send a 100k-digit id, and that must not raise here.
                return default
        if _FLOAT_TEXT.match(text):
            number = float(text)
            return int(number) if math.isfinite(number) else default
    return default


def to_float(value: Any, default: T) -> float | T:
    """Coerce `value` to a finite float, returning `default` if it is not one.

    Accepts int, bool, finite float, and a numeric string with surrounding
    whitespace. Non-finite values are REJECTED even though `float()` accepts
    them: `json.dumps` renders them as bare `Infinity`/`NaN`, which is invalid
    JSON that neither the device nor HA can read back, and an `inf` sensor
    state is worse than a missing one. Underscore separators and non-ASCII
    digits are rejected for the same reason as in `to_int`.
    """
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int):
        try:
            return float(value)
        except OverflowError:
            # int is unbounded, float is not: 10**400 has no float value.
            return default
    if isinstance(value, float):
        return value if math.isfinite(value) else default
    if isinstance(value, str):
        text = value.strip()
        if _FLOAT_TEXT.match(text):
            number = float(text)
            return number if math.isfinite(number) else default
    return default


def to_bool(value: Any, default: T) -> bool | T:
    """Coerce `value` to a bool, returning `default` if it is not one.

    Accepts real bools, the numbers 0 and 1 (the device's own on/off form),
    and the strings "1"/"0", "true"/"false", "on"/"off", "yes"/"no",
    case-insensitively and stripped — "ON"/"OFF" being what HA publishes.

    Numbers other than 0/1 return `default` rather than `bool(value)`: a field
    carrying 2 is a mode enum, not a switch, and reading it as True would hide
    the miswiring instead of surfacing it. This also means the caller decides
    what unknown input means — pass `default=False` for the old
    `_coerce_switch` behaviour of treating anything unrecognised as off.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and (value == 0 or value == 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_TEXT:
            return True
        if text in _FALSE_TEXT:
            return False
    return default
