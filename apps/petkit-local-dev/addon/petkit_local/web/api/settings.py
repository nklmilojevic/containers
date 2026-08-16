"""The Setup tab: live runtime settings, server facts, blocked attempts, caps.

`LIVE_SETTINGS` is the whole control surface for proxy mode and capture — there
is no add-on option and no CLI flag for either — so this module owns both the
list and what a valid value for each one is. `api_info` answers the rest of the
tab from one request, and `api_blocked` is the persisted half of what proxy mode
refused to pass down.

`api_retention` is here rather than with the media endpoints: it is a settings
control that reads and writes `RetentionConfig` and never touches a path.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from aiohttp import web

from petkit_local.http.proxy import DEFAULT_UPSTREAM, UPSTREAM_PRESETS
from petkit_local.utils.coerce import to_int
from petkit_local.utils.const import (
    DEVICE_NAMES, DEVICE_TYPES_AI, VERSION,
)
from petkit_local.utils.jsonio import atomic_write_json, read_json
from petkit_local.web.api._common import MAX_EVENT_LIMIT, _json_body, _limit_param, _live

log = logging.getLogger(__name__)


# Runtime settings the panel may flip live. Each is read per-request from the
# shared app config (or applied via a setter), so no restart is needed. Value is
# the coercion type.
#
# These are the ONLY control surface for proxy mode and capture: neither has an
# add-on option or a CLI flag any more. Every key here must also appear in
# `config.PANEL_LIVE_KEYS`, or it is written now and dropped at the next start.
LIVE_SETTINGS = {
    "proxy_mode": bool,
    "proxy_upstream": str,
    "proxy_dns": str,
    "proxy_block_run_cmd": bool,
    "proxy_block_ota": bool,
    "proxy_block_log_upload": bool,
    "proxy_media_real_oss": bool,
    "proxy_local_cvr_window": bool,
    "proxy_mqtt_bridge": bool,
    "proxy_only": str,
    "capture": bool,
}

#: Defaults for `LIVE_SETTINGS`, used when the shared config has no value yet.
#: Both guards default ON: proxy mode is a debugging tool, and a debugging tool
#: that lets the cloud run a command or push firmware is a liability.
LIVE_SETTING_DEFAULTS = {
    "proxy_mode": False,
    "proxy_upstream": "",
    "proxy_dns": "",
    "proxy_block_run_cmd": True,
    "proxy_block_ota": True,
    "proxy_block_log_upload": True,
    "proxy_media_real_oss": False,
    "proxy_local_cvr_window": False,
    "proxy_mqtt_bridge": True,
    "proxy_only": "",
    "capture": False,
}


def _current_settings(request: web.Request) -> dict[str, Any]:
    """The `LIVE_SETTINGS` values as they are right now, all keys always present.

    `capture` alone falls back to the static panel config as well as the
    default, because it is the one setting that also existed before the shared
    live config did.
    """
    live = _live(request)
    cfg = request.app["cfg"]
    fallbacks = dict(LIVE_SETTING_DEFAULTS)
    fallbacks["capture"] = bool(cfg.get("capture", False))

    settings = {}
    for key, typ in LIVE_SETTINGS.items():
        value = live.get(key, fallbacks[key])
        settings[key] = bool(value) if typ is bool else (value or "")
    return settings


def _valid_upstream(value: str) -> bool:
    """Whether `proxy_upstream` names something `resolve_upstream` can use.

    Validated here rather than coerced, because the failure is silent otherwise:
    a typo'd URL would be saved happily and then produce a connection error per
    device request with nothing in the panel to say why.
    """
    value = (value or "").strip()
    if not value or value in UPSTREAM_PRESETS:
        return True
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _valid_dns(value: str) -> bool:
    """Whether `proxy_dns` is an IPv4 address, optionally with a port.

    A hostname is refused on purpose. Resolving the resolver would need the
    system DNS — the one this setting exists to stop trusting — so a name here
    would work until the moment it had to.
    """
    value = (value or "").strip()
    if not value:
        return True
    host, sep, port = value.partition(":")
    if sep and not (port.isdigit() and 0 < int(port) < 65536):
        return False
    octets = host.split(".")
    return (len(octets) == 4
            and all(o.isdigit() and len(o) <= 3 and int(o) < 256 for o in octets))


#: Extra validation for settings whose value is not just a type. `api_settings`
#: coerces to bool or str; anything with a narrower domain is checked here.
LIVE_SETTING_VALIDATORS = {"proxy_upstream": _valid_upstream, "proxy_dns": _valid_dns}


async def api_settings(request: web.Request) -> web.Response:
    """GET returns the live-editable runtime settings; POST updates them in the
    shared config (immediate effect) and persists to the overrides file.

    Both methods answer `{"settings": {...}, ...}`; POST adds `changed` (only
    the keys in `LIVE_SETTINGS` — anything else in the body is ignored), 400s
    if nothing in the request was applicable, and 400s on a value that fails
    `LIVE_SETTING_VALIDATORS` **without applying any of the batch**, so a
    rejected request cannot leave half its settings written.
    """
    if request.method == "GET":
        return web.json_response({"settings": _current_settings(request), "writable": bool(request.app.get("live_config"))})

    live = request.app.get("live_config")
    if not isinstance(live, dict) or not live:
        # Nothing wired to write into (empty fallback in tests / no device app).
        return web.json_response({"error": "settings not writable in this mode"}, status=400)

    body = await _json_body(request)

    changed: dict[str, Any] = {}
    for key, val in body.items():
        typ = LIVE_SETTINGS.get(key)
        if typ is None:
            continue
        val = bool(val) if typ is bool else str(val)
        validator = LIVE_SETTING_VALIDATORS.get(key)
        if validator is not None and not validator(val):
            return web.json_response({"error": f"bad value for {key}"}, status=400)
        changed[key] = val

    if not changed:
        return web.json_response({"error": "no valid settings in request"}, status=400)

    # Applied only once the whole batch validated.
    live.update(changed)

    # Persist overrides so panel changes survive a restart (merge with existing).
    path = request.app["cfg"].get("settings_path")
    if path:
        try:
            # A damaged overrides file is already dead weight — config.py's
            # apply_panel_overrides ignores it wholesale on a parse error — so
            # rewriting it from the current settings is a repair, not data loss.
            existing = read_json(path, {})
            if not isinstance(existing, dict):
                existing = {}
            existing.update(changed)
            atomic_write_json(path, existing)
        except OSError as e:
            log.warning("panel: could not persist settings overrides: %s", e)

    log.info("panel: runtime settings changed: %s", changed)
    return web.json_response({"ok": True, "changed": changed, "settings": _current_settings(request)})


#: How much of a blocked payload is shown before `?reveal=1`.
MASK_PREFIX = 6


def _mask(value: str | None) -> str | None:
    """Shorten a recorded payload to a recognisable stub.

    These rows can hold a real `deviceSecret` or a media AES key, and this panel
    is served unauthenticated on the HTTPS port (see `web/panel.py`'s module
    docstring). The full value stays in `/data` — the database and the capture
    files — which is the same trust level as `devices.json`.
    """
    if not value:
        return value
    if len(value) <= MASK_PREFIX:
        return f"… ({len(value)} chars)"
    return f"{value[:MASK_PREFIX]}… ({len(value)} chars)"


async def api_blocked(request: web.Request) -> web.Response:
    """What the real cloud tried to do to a device, and did not get to do.

    `GET /api/blocked` — the persisted subset of proxy mode's redactions: shell
    commands, firmware pushes and credential swaps. Routine address
    substitutions are NOT here (they would be thousands of rows a day); the
    Setup tab shows those as counters from `/api/info`.

    On a healthy proxied session this is EMPTY. A row means the upstream
    actually tried something.

    Answers `{records: [...], counts: {...}}`. `payload_json` is masked unless
    `?reveal=1`, `?limit=` is clamped, and `?device=` / `?kind=` filter.
    """
    store = request.app.get("event_store")
    if store is None:
        return web.json_response({"error": "no event store"}, status=400)

    device = request.query.get("device")
    rows = await store.recent_blocked_attempts(
        limit=_limit_param(request, 200, MAX_EVENT_LIMIT),
        device_id=to_int(device, None) if device else None,
        kind=request.query.get("kind") or None,
    )
    if request.query.get("reveal") != "1":
        rows = [{**r, "payload_json": _mask(r.get("payload_json"))} for r in rows]

    return web.json_response({"records": rows,
                              "counts": request.app["hub"].redaction_counts()})


async def api_info(request: web.Request) -> web.Response:
    """Server-wide facts for the Setup tab.

    The URLs/ports a device must dial, whether the TLS cert exists, bridge
    liveness, device count, and the current runtime settings — those last are
    `api_settings`' territory and are mirrored here only so the frontend can
    render the whole tab from one request.
    """
    # Imported in the call rather than at module scope: `panel.py` renders the
    # page this hash is stamped into, and it imports this module for its route
    # table — so the dependency only runs one way at import time.
    from petkit_local.web.panel import ASSET_VERSION  # noqa: PLC0415

    cfg = request.app["cfg"]
    reg = request.app["registry"]
    cert = cfg.get("cert_path", "")
    ha_pub = request.app.get("ha_publisher")
    return web.json_response({
        # The hash of the assets THIS process would serve, so the panel can
        # compare it with the one baked into the page it is running from. They
        # are the same value from two different moments: `asset_version` in the
        # markup came with the document, possibly out of a cache, while this
        # one is answered live. A mismatch is the one failure the `version`
        # below cannot see — a fresh server running behind a stale page.
        "asset_version": ASSET_VERSION,
        # The running version. First thing to check when a device reports the
        # entities of a release you thought you had replaced.
        "version": VERSION,
        "api_url": cfg.get("api_url"),
        "mqtt_tls": cfg.get("mqtt_tls"),
        "mqtt_tls_port": cfg.get("mqtt_tls_port"),
        "mqtt_port": cfg.get("mqtt_port"),
        "strict_auth": cfg.get("strict_auth"),
        "cert_exists": bool(cert) and os.path.exists(cert),
        # The HA publisher, NOT the device-facing bridge. This used to read
        # `app["bridge"]._client`, which is the connection to our own embedded
        # broker -- up whenever the broker is, regardless of whether anything
        # reaches Home Assistant. Running with `--no-ha`, where there is no
        # publisher at all, it still reported a green "connected".
        "ha_publishing": bool(ha_pub and ha_pub.connected),
        # Whether publishing is configured at all, so the panel can say
        # "disabled" rather than "down" when it was switched off deliberately.
        "ha_enabled": ha_pub is not None,
        "device_count": len(reg.all()),
        # live-editable runtime settings (reflect the shared device-facing config)
        "settings": _current_settings(request),
        "settings_writable": bool(request.app.get("live_config")),
        "capture": _current_settings(request)["capture"],
        # The products that do on-device recognition, so the AI/Pets tab can
        # name them instead of carrying its own copy of the list. Sorted for a
        # stable UI; a codename with no marketing name is skipped rather than
        # shown as "Unknown".
        "ai_device_names": sorted(
            DEVICE_NAMES[c] for c in DEVICE_TYPES_AI if c in DEVICE_NAMES),
        "upstreams": _upstream_choices(),
        # Per-rule totals since start. The routine substitutions live only here
        # and in the capture files — see events/models.py::BlockedAttempt for
        # why they are not rows in the database.
        "redactions": request.app["hub"].redaction_counts(),
        # Proxied-call outcomes. The answer to "is proxy mode actually doing
        # anything?", which nothing else answers once a taken-over device
        # settles into polling only the heartbeat.
        "upstream": request.app["hub"].upstream_counts(),
    })


def _upstream_choices() -> list[dict[str, str | bool]]:
    """The upstream servers the Setup tab offers.

    Built from `http/proxy.py::UPSTREAM_PRESETS` so the panel cannot drift from
    what `resolve_upstream` actually accepts. `default` marks the one an empty
    setting resolves to, so the picker can show it selected without having to
    know which key that is.
    """
    return [{"key": k, "url": v, "default": k == DEFAULT_UPSTREAM}
            for k, v in UPSTREAM_PRESETS.items()]


async def api_retention(request: web.Request) -> web.Response:
    """GET/POST the per-capability media retention caps (media/retention.py).

    Answers `{"retention": {...}}`. POST hands the body straight to
    `RetentionConfig.update`, which is what validates it, and persists the
    result so the next sweep uses it.
    """
    retention = request.app.get("retention_config")
    if retention is None:
        return web.json_response({"error": "retention not available"}, status=400)

    if request.method == "GET":
        return web.json_response({"retention": retention.data})

    body = await _json_body(request)

    retention.update(body)
    data_dir = request.app["cfg"].get("data_dir", "/data")
    retention.save(data_dir)
    return web.json_response({"ok": True, "retention": retention.data})
