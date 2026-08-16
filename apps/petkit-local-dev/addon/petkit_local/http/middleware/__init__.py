"""Request-scoped identity, proxying and observability for the device-facing server.

Three middlewares, in the order aiohttp applies them (outermost first):

* `logging_middleware` — the one place every device request is logged, mirrored
  to the panel's live log and (optionally) written to a capture file, and the
  one place a device's `online`/`last_seen` is refreshed. Liveness lives here
  rather than in the handlers because *any* HTTP contact proves the device is
  up, and not every device model does MQTT or even an HTTP heartbeat.
* `device_middleware` — parses the firmware's `X-Device` header and the URL
  into ``request["x_device"]``, ``request["api_version"]`` and
  ``request["device_type"]``, so no handler has to re-derive them.
* `proxy_middleware` — when proxy mode is on, forwards the request to the real
  PetKit cloud and answers with its (redacted) reply instead of ours.

That order is load-bearing in both directions. `device_middleware` runs before
the proxy so `request["device_type"]` is set when the upstream is being chosen,
and `logging_middleware` stays outermost so the panel's live log and the capture
file record **what the device actually received**, not what our handler would
have said.

Nothing here rejects a request: identity is best-effort and every key it sets is
optional, because a device that cannot be identified must still get a valid
answer (see `http/server.py`'s "never 404 a device" rule). The proxy holds the
same line — every failure path falls back to the local answer.

One module each — `device.py`, `logging.py`, `proxy.py` — plus the backstop
below and the three values all of them share. Those are defined before the
submodules are imported, because each one imports them back from here.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from aiohttp import web

log = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

#: Only the PetKit API prefix is device protocol traffic. The bucket, the face
#: photos and the patcher downloads share this app and must never be forwarded
#: upstream, logged as device traffic, or counted as liveness.
API_PREFIX = "/6/"

#: Where `proxy_middleware` leaves what it did, for `logging_middleware` to fold
#: into the entry it was already going to write.
PROXY_OUTCOME = "proxy_outcome"

# Below the three values above on purpose: every submodule imports them back
# from here. Importing `.logging` also rebinds the name `logging` on this module
# to that submodule, which is why `log` is built first and nothing after this
# point reaches for the stdlib module by name.
from petkit_local.http.middleware.device import (
    device_middleware,
    parse_form_body,
    parse_x_device,
)
from petkit_local.http.middleware.logging import _short, _text_or_none, logging_middleware
from petkit_local.http.middleware.proxy import (
    _build_policy,
    _capture_exchange,
    _endpoint_selected,
    _is_heartbeat,
    _local_socket,
    _note_outcome,
    _record_exchange,
    _remember_upstream_credentials,
    _reports_a_local_log_upload,
    _FORWARDED_HEADERS,
    GUARDED_LOCAL_ENDPOINTS,
    LOCAL_ONLY_ENDPOINTS,
    proxy_middleware,
)

#: Everything importable from this package, whether it is public API or a name
#: a test or another module reaches for. Listed so the re-exports above are not
#: read as unused imports.
__all__ = [
    "API_PREFIX",
    "GUARDED_LOCAL_ENDPOINTS",
    "Handler",
    "LOCAL_ONLY_ENDPOINTS",
    "PROXY_OUTCOME",
    "device_middleware",
    "logging_middleware",
    "never_fail_middleware",
    "parse_form_body",
    "parse_x_device",
    "proxy_middleware",
    "_FORWARDED_HEADERS",
    "_build_policy",
    "_capture_exchange",
    "_endpoint_selected",
    "_is_heartbeat",
    "_local_socket",
    "_note_outcome",
    "_record_exchange",
    "_remember_upstream_credentials",
    "_reports_a_local_log_upload",
    "_short",
    "_text_or_none",
]


@web.middleware
async def never_fail_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    """Turn any unhandled handler exception into an empty success.

    `http/server.py`'s rule is that a device is never given a 4xx or 5xx,
    because the firmware reads one as a server fault and retries forever. That
    rule was enforced by every handler individually catching its own failures —
    and several could still escape: a SQLAlchemy error from `upsert_event` on a
    full or read-only disk, an unreadable media key file in `handle_oss_sts`, an
    `MqttError` from the HA publisher during a broker restart, a `RecursionError`
    from deeply nested JSON. Each of those became aiohttp's default 500, i.e.
    precisely the retry loop the rule exists to prevent.

    So this is the backstop, outermost of the four. It answers `{"result": {}}`,
    the same shape `handle_catchall` uses for an endpoint we do not implement —
    a device treats it as "nothing to do" and moves on.

    It deliberately does NOT swallow:

    * `web.HTTPException` — a handler that returns a status on purpose (the
      bucket's 403 refusals, a redirect) keeps it.
    * `asyncio.CancelledError` — shutdown must stay prompt.

    The log line is ERROR with a traceback: this firing is always a bug worth
    fixing, and swallowing it silently would hide exactly the failures the rule
    is protecting the device from.
    """
    try:
        return await handler(request)
    except (web.HTTPException, asyncio.CancelledError):
        raise
    except Exception:
        log.exception("Unhandled error in %s %s - answering empty success so the "
                      "device does not retry forever", request.method, request.path)
        return web.json_response({"result": {}})
