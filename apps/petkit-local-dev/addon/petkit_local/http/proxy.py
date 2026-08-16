"""Proxy mode: forward device requests to the official PetKit API server.

This is the observation mode, and since the rework it forwards EVERYTHING —
`http/middleware/proxy.py::proxy_middleware` runs it for every `/6/` request, not just
the handful nobody implemented. A device therefore runs against the real cloud
while its whole conversation passes through us.

What the device is allowed to receive back is not decided here: `http/redact/`
owns that, and this module only carries bytes and reports what happened. The
split matters because the MQTT bridge needs the same rules on frames that never
touch HTTP.

Two things this module does own:

* **Which upstream.** `resolve_upstream` turns the panel's setting — a preset
  key or a URL — into a base URL. `normalize_upstream` is not cosmetic: request
  paths already start with `/6/`, so a base that ends in `/6/` would build
  `/6/6/…`.
* **Not making things worse when upstream is sick.** Every request now waits on
  PetKit, so the timeout is short and a run of failures trips a breaker that
  stops trying for a minute. The caller falls back to our own answer; a device
  must never see the upstream's outage (see `http/server.py`'s never-404 rule).

The upstream connection is pooled per aiohttp application — see
`get_proxy_session`. `main.py` should register `close_proxy_session` on the
device-facing app's cleanup signal:

    app.on_cleanup.append(close_proxy_session)

which must happen before `AppRunner.setup()` freezes the app.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from petkit_local.http.dns import UpstreamResolver
from petkit_local.http.redact import Redaction, RedactionPolicy, cloud_error, redact_body

if TYPE_CHECKING:  # pragma: no cover - typing only
    from petkit_local.devices.base import Device

log = logging.getLogger(__name__)

#: The servers offered in the panel, one per PetKit region. Written the way a
#: human would paste them, `/6/` and all — `normalize_upstream` strips that back
#: off, because the request path already carries it.
#:
#: Each of these presents a certificate issued to 小佩网络科技(上海)有限公司,
#: PetKit's own company, so they are its endpoints and not merely names that look
#: like them. Which one holds a given device is decided when the device is
#: registered, not by where its owner lives; PetKit moves a device between them
#: through `dev_serverinfo`'s `apiServers`, which redaction blocks from reaching
#: it (`http/redact/`).
UPSTREAM_PRESETS = {
    "petkit-eu": "https://api-eu.petkt.com/6/",
    "petkit-americas": "https://api.petkt.com/6/",
    "petkit-asia": "https://api.petktasia.com/6/",
    "petkit-cn": "https://api.petkit.cn/6/",
    "petkit-ru": "https://api-ru.petkit.cn/6/",
}

#: Where an unset `proxy_upstream` points. There is no picking this per device:
#: a device is handed its API server during BLE provisioning, so its region is a
#: property of that device and not of its model, and nothing it sends says which
#: one it was given. This is a starting point for someone turning proxy mode on,
#: and the only one with a device behind it — an Ingenic T5 has run against it
#: through this proxy with its replies recorded.
DEFAULT_UPSTREAM = "petkit-eu"

# Everything is forwarded now, so a hung upstream delays every device call, not
# just the unimplemented ones. A device heartbeats every ~15s; 8s leaves room to
# fall back to the local answer before the next poll arrives.
UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=8)

# After this many consecutive failures, stop trying for `BREAKER_COOLDOWN`
# seconds. Without it a dead upstream adds the full timeout to every single
# request for as long as proxy mode is left on.
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = 60.0

# Request headers worth forwarding. Copying the rest would leak our own hop's
# Host and encoding negotiation upstream.
_FORWARD_HEADERS = ("X-Device", "X-Session", "F-Session", "User-Agent", "Content-Type")

# Response headers that describe OUR framing, not upstream's: aiohttp re-frames
# the body itself and a stale value here would describe it wrongly.
_HOP_BY_HOP = ("Transfer-Encoding", "Content-Encoding", "Content-Length")

# Keyed by application rather than stored in `app["proxy_session"]`: the session
# is created lazily on the first proxied request, by which point AppRunner has
# frozen the app, and writing to a frozen Application is deprecated in aiohttp 3
# and an error in aiohttp 4. Weak keys so a discarded app (every aiohttp test
# server) does not keep its session object alive.
_SESSIONS: weakref.WeakKeyDictionary[web.Application, aiohttp.ClientSession] = (
    weakref.WeakKeyDictionary()
)

# Same weak-keyed lifetime, for the circuit breaker's [failures, open_until].
_BREAKERS: weakref.WeakKeyDictionary[web.Application, list[float]] = (
    weakref.WeakKeyDictionary()
)

# The `proxy_dns` each live session was built with. The resolver is fixed at
# connector construction, so this is what notices the setting changing.
_SESSION_DNS: weakref.WeakKeyDictionary[web.Application, str] = (
    weakref.WeakKeyDictionary()
)

# Strong references to the background closes started by `get_proxy_session`.
# The event loop only holds a weak one, so a task nothing else names can be
# collected mid-close, leaving the old connector's sockets open.
_CLOSING: set[asyncio.Task[None]] = set()


@dataclass
class Exchange:
    """One completed round trip to the real cloud.

    `upstream_body` is what PetKit actually sent and `body` is what the device
    may see; keeping both is the point of proxy mode, and the capture stream
    writes the pair side by side.
    """

    url: str
    status: int
    headers: dict[str, str]
    upstream_body: bytes
    body: bytes
    records: list[Redaction] = field(default_factory=list)
    captured: dict[str, Any] = field(default_factory=dict)
    #: PetKit's refusal envelope, when that is what came back — see
    #: `redact.cloud_error`. Carried separately from `status` because it arrives
    #: with HTTP 200.
    error: dict | None = None

    @property
    def blocked(self) -> list[Redaction]:
        """The records worth persisting — see `redact.BLOCKING_RULES`."""
        return [r for r in self.records if r.blocking]

    @property
    def usable(self) -> bool:
        """Whether this reply is one a device can actually act on.

        False for a transport-level failure AND for a 200 that carries a refusal
        — the two are the same thing from the device's point of view, and both
        mean the caller should serve its own answer instead.
        """
        return 200 <= self.status < 300 and self.error is None

    def to_response(self) -> web.Response:
        """The redacted reply, as something aiohttp can send to the device."""
        return web.Response(body=self.body, status=self.status, headers=self.headers)


def normalize_upstream(base: str) -> str:
    """Strip a base URL back to scheme+host, dropping a trailing API version.

    `forward` builds `base + request.path`, and a device's path already begins
    `/6/`. A base written the natural way — `https://api-eu.petkt.com/6/` — would
    therefore produce `/6/6/dev_serverinfo`, which upstream answers with a 404
    that firmware retries forever. Only a whole `6` segment is removed, so a
    host whose path genuinely ends in something like `/v6` is left alone.
    """
    trimmed = (base or "").strip().rstrip("/")
    if trimmed.endswith("/6"):
        trimmed = trimmed[: -len("/6")]
    return trimmed


def resolve_upstream(setting: str) -> str:
    """Turn the panel's `proxy_upstream` setting into a base URL.

    Args:
        setting: A key of `UPSTREAM_PRESETS`, `""` for `DEFAULT_UPSTREAM`, or a
            full URL typed into the panel. A free-text URL is still accepted, and
            is how you reach a server by address — which is what a LAN that
            redirects PetKit's names to this add-on leaves you needing.

    There is deliberately no per-generation choice: `api.eu-pet.com` and
    `api-eu.petkt.com` are two names for one server, answering on the same pair
    of addresses, and provisioning decides the region anyway — not the firmware
    family.
    """
    value = (setting or "").strip() or DEFAULT_UPSTREAM
    if value in UPSTREAM_PRESETS:
        return normalize_upstream(UPSTREAM_PRESETS[value])
    return normalize_upstream(value)


def get_proxy_session(app: web.Application, dns_server: str = "") -> aiohttp.ClientSession:
    """Return the shared upstream session for `app`, creating it on first use.

    One session per app means one connector pool and one DNS cache for the whole
    process; a session per request would re-resolve and re-handshake TLS with the
    PetKit cloud on every single proxied call.

    Args:
        dns_server: `proxy_dns`. Empty uses the system resolver. The resolver
            lives on the connector, so a change to this setting has to build a
            new session — the old one is closed in the background rather than
            awaited, because callers are mid-request and a settings change is
            not a reason to make a device wait.

    Safe to call concurrently: `ClientSession()` does not await, so no two
    coroutines can interleave between the lookup and the store.
    """
    session = _SESSIONS.get(app)
    if session is not None and not session.closed and _SESSION_DNS.get(app, "") != dns_server:
        closing = asyncio.ensure_future(session.close())
        _CLOSING.add(closing)
        closing.add_done_callback(_CLOSING.discard)
        session = None

    if session is None or session.closed:
        connector = None
        if dns_server:
            connector = aiohttp.TCPConnector(resolver=UpstreamResolver(dns_server))
        session = aiohttp.ClientSession(timeout=UPSTREAM_TIMEOUT, connector=connector)
        _SESSIONS[app] = session
        _SESSION_DNS[app] = dns_server
    return session


async def close_proxy_session(app: web.Application) -> None:
    """Close `app`'s upstream session if one was ever opened.

    Signature matches aiohttp's cleanup-signal callback, so it can be used
    directly as `app.on_cleanup.append(close_proxy_session)`. A later
    `get_proxy_session` simply opens a new one, so calling this early is
    harmless.
    """
    _BREAKERS.pop(app, None)
    _SESSION_DNS.pop(app, None)
    session = _SESSIONS.pop(app, None)
    if session is not None and not session.closed:
        await session.close()


def breaker_is_open(app: web.Application, now: float | None = None) -> bool:
    """Whether forwarding is currently being skipped because upstream is down."""
    state = _BREAKERS.get(app)
    if state is None:
        return False
    return state[1] > (now if now is not None else time.monotonic())


def _record_outcome(app: web.Application, ok: bool) -> None:
    """Advance the breaker: any success closes it, `BREAKER_THRESHOLD` opens it."""
    state = _BREAKERS.setdefault(app, [0.0, 0.0])
    if ok:
        state[0] = 0.0
        state[1] = 0.0
        return
    state[0] += 1
    if state[0] >= BREAKER_THRESHOLD:
        state[1] = time.monotonic() + BREAKER_COOLDOWN
        log.warning("PROXY: upstream failed %d times in a row, pausing for %.0fs",
                    int(state[0]), BREAKER_COOLDOWN)


async def forward(
    request: web.Request,
    *,
    body: bytes,
    upstream: str,
    policy: RedactionPolicy,
    dns_server: str = "",
) -> Exchange | None:
    """Send one request to the real cloud and bring back a redacted reply.

    Args:
        body: The request body, already read. The caller owns reading it,
            because it also needs the bytes for capture and because the local
            handler must be free to read the (cached) payload itself.
        upstream: A base URL from `resolve_upstream` — scheme and host only.
        policy: What the reply may contain, see `http/redact/`.
        dns_server: `proxy_dns`, for a LAN whose own DNS answers PetKit's names
            with this add-on. Empty uses the system resolver. See `http/dns.py`.

    Returns:
        The `Exchange`, or None when the upstream could not be reached, answered
        too slowly, or the breaker is open. None means "we have nothing from
        PetKit"; the caller answers from our own handler instead, so a device
        never pays for the cloud being down. A 502 is never manufactured
        here: handing one to a device IS making it pay.
    """
    if breaker_is_open(request.app):
        return None

    target_url = f"{upstream}{request.path}"
    if request.query_string:
        target_url += f"?{request.query_string}"

    headers = {h: request.headers[h] for h in _FORWARD_HEADERS if h in request.headers}

    session = get_proxy_session(request.app, dns_server)
    try:
        async with session.request(
            method=request.method,
            url=target_url,
            headers=headers,
            data=body if body else None,
            ssl=False,
        ) as resp:
            raw = await resp.read()
            resp_headers = {k: v for k, v in resp.headers.items() if k not in _HOP_BY_HOP}
    except Exception as e:
        # Includes the timeout. Logged at WARNING and not ERROR: with proxy mode
        # on this is a routine outcome, and the device is served regardless.
        log.warning("PROXY %s %s failed: %s", request.method, target_url, e)
        _record_outcome(request.app, ok=False)
        return None

    _record_outcome(request.app, ok=True)
    redacted = redact_body(raw, endpoint=request.path, policy=policy)
    error = cloud_error(raw)

    log.info("PROXY %s %s -> %s [%d]%s%s", request.method, request.path, target_url,
             resp.status,
             f" error:{error.get('code')}" if error else "",
             f" redacted:{','.join(r.rule for r in redacted.records)}" if redacted.records else "")

    return Exchange(
        url=target_url,
        status=resp.status,
        headers=resp_headers,
        upstream_body=raw,
        body=redacted.body,
        records=redacted.records,
        captured=redacted.captured,
        error=error,
    )


async def proxy_request(
    request: web.Request,
    upstream: str | None = None,
    block_run_cmd: bool = True,
    *,
    device: Device | None = None,
) -> web.Response:
    """Forward one request and answer with the upstream's reply, or a 502.

    The pre-rework entry point, kept for callers that want a `web.Response` and
    have no local answer to fall back on. `proxy_middleware` uses `forward`
    directly instead, because it always has one.

    Only `block_run_cmd` is honoured from the redaction rules — the rest need a
    registered device to substitute values from, and this signature has no way
    to know one. Pass `device` to get the full policy.

    Returns:
        The redacted upstream response, or a 502 carrying ``{"result": {},
        "error": ...}`` when it could not be reached — an empty result rather
        than a bare error body, so firmware that only looks at `result` sees the
        same shape it always does.
    """
    base = normalize_upstream(upstream) if upstream else resolve_upstream("")

    try:
        body = await request.read()
    except Exception:
        body = b""

    policy = RedactionPolicy(device=device, block_rce=block_run_cmd, block_ota=device is not None)
    exchange = await forward(request, body=body, upstream=base, policy=policy)
    if exchange is None:
        return web.json_response({"result": {}, "error": "upstream unreachable"}, status=502)
    return exchange.to_response()


def merge_json_result_lists(primary: bytes, secondary: bytes) -> bytes | None:
    """Concatenate two ``{"result": [...]}`` bodies, `primary`'s entries first.

    Shared by the heartbeat merge (`handlers/heartbeat.py`) and kept here beside
    the rest of the wire-format handling.

    Returns:
        The merged body, or None when either side is not a JSON object with a
        list `result` — the caller then decides which one to send whole, rather
        than getting a silently mangled shape.
    """
    try:
        left = json.loads(primary)
        right = json.loads(secondary)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    if not isinstance(left.get("result"), list) or not isinstance(right.get("result"), list):
        return None

    merged = dict(left)
    merged["result"] = left["result"] + right["result"]
    return json.dumps(merged).encode()
