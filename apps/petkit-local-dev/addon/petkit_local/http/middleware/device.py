"""Who is calling: the `X-Device` header, the POST body and the URL.

A device puts its identity in up to three places and no model uses all of them,
so all three are read here, once, into request keys the handlers can rely on.
Nothing in this module rejects anything — identity is best-effort, and a device
that cannot be identified must still get a valid answer.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs

from aiohttp import web

from petkit_local.http.middleware import Handler


def parse_x_device(header: str) -> dict | None:
    """Parse the firmware's `X-Device` header into a flat dict of fields.

    The header is query-string shaped — ``id=10000001&type=T5&sn=...&...`` — so
    it is decoded with `parse_qs`. If that yields no ``id``, the header is
    re-split on raw ``&``/``=`` (values left percent-encoded) and that result is
    used instead, but only when it does produce one.

    Returns:
        The header's fields keyed by name, or None when no ``id`` field could be
        recovered at all — such a header identifies nothing, and callers treat
        it exactly like an absent header rather than trusting the other fields.
    """
    if not header:
        return None
    parsed = parse_qs(header, keep_blank_values=True)
    result = {k: v[0] for k, v in parsed.items() if v}
    if "id" not in result:
        parts = dict(p.split("=", 1) for p in header.split("&") if "=" in p)
        if "id" in parts:
            return parts
        return None
    return result


async def parse_form_body(request: web.Request) -> dict[str, str]:
    """The urlencoded POST body as a flat dict, or {} for anything else.

    A third place a device may put its identity, and for some of them the only
    one. An ESP32 feeder signs up with no `X-Device` header and no query string
    at all -- everything is in the body::

        POST /6/d4/dev_signup   Content-Type: application/x-www-form-urlencoded
        hardware=1&firmware=1.267&mac=...&id=400090690&sn=20241223G11497&...

    Read here rather than in the handlers so `_common.py`'s accessors stay
    synchronous and every endpoint gets it at once, exactly as `X-Device` is.

    Costs nothing: `logging_middleware` already reads the body of every POST
    under `/6/`, and aiohttp caches it, so this is the same bytes a second time.
    It cannot starve `proxy_middleware` either -- that copies the body into a
    local before calling the handler.

    Never raises. A body that is not urlencoded, not decodable, or simply
    absent yields {}, which reads the same as a device that sent nothing.
    """
    if request.method not in ("POST", "PUT", "PATCH"):
        return {}
    ctype = request.headers.get("Content-Type", "")
    if "application/x-www-form-urlencoded" not in ctype.lower():
        return {}
    try:
        raw = await request.read()
    except Exception:  # noqa: BLE001 - a body we cannot read is a body we ignore
        return {}
    if not raw:
        return {}
    try:
        parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
    except Exception:  # noqa: BLE001 - device input never raises
        return {}
    return {k: v[0] for k, v in parsed.items() if v}


@web.middleware
async def device_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Attach the requester's identity to the request, then hand it on.

    Sets, when derivable: ``request["x_device"]`` (the parsed `X-Device` header,
    only if it carries an id), ``request["form"]`` (the urlencoded POST body),
    ``request["api_version"]`` and ``request["device_type"]``. All are optional
    — handlers use `handlers/_common.py` to resolve a device and answer sensibly
    when none of this is present.
    """
    info = None
    x_device = request.headers.get("X-Device", "")
    if x_device:
        info = parse_x_device(x_device)
        if info and "id" in info:
            request["x_device"] = info

    request["form"] = await parse_form_body(request)

    path = request.path
    poll_m = re.match(r"^/(\d+)/poll/(\w+)/", path)
    m = re.match(r"^/(\d+)/(\w+)/", path)
    if poll_m:
        request["api_version"] = poll_m.group(1)
        request["device_type"] = poll_m.group(2)
    elif m and m.group(2) != "poll":
        request["api_version"] = m.group(1)
        request["device_type"] = m.group(2)

    # Fallback: device type from the X-Device `type` field (paths that omit it,
    # e.g. /6/dev_serverinfo or /6/poll/heartbeat).
    if "device_type" not in request and info and info.get("type"):
        request["device_type"] = info["type"].lower()

    return await handler(request)
