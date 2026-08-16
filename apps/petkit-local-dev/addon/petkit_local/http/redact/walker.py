"""Where the rules run: the recursive descent over one decoded body.

Every rule in `rules.py` matches on the SHAPE of an object, so something has to
carry them to every object there is — including the ones hidden inside a JSON
document that arrived encoded as a string, which is how the heartbeat carries
its commands. That is all this module does; it decides nothing about content.
"""
from __future__ import annotations

import json
from typing import Any

from petkit_local.http.redact import _DROP, RedactionPolicy, RedactionResult
from petkit_local.http.redact.rules import (
    _match_cvr_capacity, _match_locale, _match_mqtt, _match_oss_sts, _match_ota_shape,
    _match_rce, _match_secret,
    _match_server,
)


# --- the walker -------------------------------------------------------------


def _walk(node: Any, path: str, policy: RedactionPolicy,
          out: RedactionResult, *, in_list: bool) -> Any:
    """Rewrite one node, returning its replacement or `_DROP`.

    `in_list` is what makes a hostile heartbeat entry disappear whole. A dropped
    value inside a plain object just loses that key, but inside a list element
    the element itself goes — which is the shape the firmware iterates over in
    `result[]`, where a stripped-but-present entry would still be a command.
    """
    if isinstance(node, dict):
        return _walk_dict(node, path, policy, out, in_list=in_list)
    if isinstance(node, list):
        cleaned = []
        for i, item in enumerate(node):
            got = _walk(item, f"{path}[{i}]", policy, out, in_list=True)
            if got is not _DROP:
                cleaned.append(got)
        return cleaned
    if isinstance(node, str):
        return _walk_json_string(node, path, policy, out)
    return node


def _walk_dict(node: dict, path: str, policy: RedactionPolicy,
               out: RedactionResult, *, in_list: bool) -> Any:
    """Apply every rule to one object, then descend into what is left."""
    if _match_rce(node, path, policy, out):
        return _DROP
    if _match_ota_shape(node, path, policy, out):
        return _DROP

    node = _match_server(node, path, policy, out)
    node = _match_mqtt(node, path, policy, out)
    node = _match_oss_sts(node, path, policy, out)
    node = _match_cvr_capacity(node, path, policy, out)
    node = _match_secret(node, path, policy, out)
    node = _match_locale(node, path, policy, out)

    cleaned: dict[str, Any] = {}
    tainted = False
    for key, value in node.items():
        got = _walk(value, f"{path}.{key}" if path else str(key), policy, out, in_list=False)
        if got is _DROP:
            tainted = True
            continue
        cleaned[key] = got

    # A list element that lost a child loses itself: `{"time": .., "content":
    # "<the command>"}` must not survive as a bare timestamp.
    if tainted and in_list:
        return _DROP
    return cleaned


def _walk_json_string(node: str, path: str, policy: RedactionPolicy,
                      out: RedactionResult) -> Any:
    """Descend into a JSON object/array that arrived encoded as a string.

    This is how the heartbeat delivers commands (`handlers/heartbeat.py`), so it
    is the single most important case in the walker. Only `{`/`[` bodies are
    considered, so a numeric or quoted string is never silently retyped, and the
    string is re-encoded only when something actually changed.
    """
    stripped = node.lstrip()
    if stripped[:1] not in ("{", "["):
        return node
    try:
        inner = json.loads(node)
    except (json.JSONDecodeError, ValueError):
        return node
    if not isinstance(inner, (dict, list)):
        return node

    got = _walk(inner, path, policy, out, in_list=False)
    if got is _DROP:
        return _DROP
    if got == inner:
        return node
    # Compact: a nested JSON string re-encoded here rides inside a device-facing
    # MQTT frame, whose parser is whitespace-strict (`mqtt/bridge.py::_dumps`).
    # Harmless on the HTTP path, which tolerates whitespace and only reaches
    # this re-encode when a value actually changed.
    return json.dumps(got, separators=(",", ":"))
