"""Safe lookups into the loosely-typed nested dicts the devices send.

Device payloads (state reports, MQTT properties, event/media metadata) are
untrusted JSON: a level may be missing, may be an explicit ``null``, or may be
a scalar where a sub-object was expected. Every package needs the same three
traversals, and this is the one implementation of them: :func:`dig` for a
nested lookup, :func:`dig_path` for a dotted one, and :func:`first_of` for a
field the device spells several ways in the same payload.

``events/normalize.py``'s ``_content_of`` / ``_as_dict`` are deliberately NOT
covered here: they parse JSON *strings* (including the bare-token repair for
the device's invalid JSON), which is decoding, not traversal.

**Absent vs. present-but-None.** The two are not the same, and every function
here distinguishes them:

* :func:`dig` / :func:`dig_path` test *key presence*, so a key that is present
  with an explicit ``null`` yields ``None`` — the reported value — and never
  the caller's ``default``. Descending *through* an explicit ``null`` still
  yields ``default``, because ``None`` is not a mapping.
* :func:`first_of` treats both ``None`` and ``""`` as "not present" and keeps
  scanning, because the device pads unused alternative spellings of a field
  with empty values rather than omitting them.

Lists are intentionally not traversable by integer index: no call site needs
it, and every current path is dict-only. A list encountered mid-path is just a
non-mapping level and yields ``default``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def dig(data: Any, *keys: Any, default: Any = None) -> Any:
    """Descend through nested mappings, returning ``default`` on any miss.

    Args:
        data: Any value; only mappings can be descended into.
        keys: One key per level, outermost first. With no keys, ``data`` is
            returned unchanged (no mapping check is applied).
        default: Returned when a level is missing or is not a mapping.

    Returns:
        The value at the end of the path. A key present with value ``None``
        returns ``None``, not ``default`` — the caller can tell an explicit
        device-sent null from an absent key by passing a non-``None``
        ``default``.
    """
    current = data
    for key in keys:
        if isinstance(current, Mapping) and key in current:
            current = current[key]
        else:
            return default
    return current


def dig_path(data: Any, path: str, default: Any = None) -> Any:
    """Descend using a dotted path such as ``"state.boxState"``.

    This is the form entity ``value_path`` definitions are written in, so the
    panel can resolve the same string HA's value_template reads.

    Args:
        path: Dot-separated keys. An empty path yields ``default``, rather than
            ``data`` as ``dig()`` with no keys would — an entity with no
            ``value_path`` has no value, it does not have the whole document as
            its value.

    Returns:
        The value at ``path``, or ``default``. Explicit ``None`` values are
        preserved exactly as in :func:`dig`.
    """
    if not path:
        return default
    return dig(data, *path.split("."), default=default)


def first_of(data: Any, *keys: Any, default: Any = None) -> Any:
    """Return the value of the first key that carries actual content.

    For fields the device spells several ways in the same payload (e.g.
    ``eventId`` / ``event_id`` / ``eventid``). This scans *alternatives* at one
    level; it does not descend.

    Replaces ``events/ingest.py::_first`` — note the signature change from a
    single tuple argument to varargs, so an existing constant tuple migrates as
    ``first_of(form, *EVENT_ID_KEYS, default="")``.

    Args:
        keys: Candidate keys in preference order.

    Returns:
        The first value that is present and is neither ``None`` nor ``""``,
        else ``default``. Unlike :func:`dig`, an explicit ``null`` does not end
        the search: the device pads the spellings it did not use with empty
        values, so a null here means "not this one", not "reported as empty".
        Falsy-but-real values (``0``, ``False``) are content and are returned.
    """
    if not isinstance(data, Mapping):
        return default
    for key in keys:
        if key in data:
            value = data[key]
            if value is not None and value != "":
                return value
    return default
