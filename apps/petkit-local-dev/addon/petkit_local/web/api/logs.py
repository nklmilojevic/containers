"""Everything the operator reads rather than sets: live events, captures, logs.

Three sources, one tab each. The hub's ring is polled once on load and streamed
after that (`api_events`, `api_ws`). The capture browser reads the JSON-lines
files capture mode writes. The device-log browser reads what a device uploaded
to `http/bucket.py`.

The last two both walk files an unauthenticated listener wrote, so their reads
happen in a thread — this process also serves the devices, and blocking here
stalls their HTTP server and the MQTT bridge with it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import Any
from urllib.parse import quote

from aiohttp import web

from petkit_local.utils.coerce import to_int
from petkit_local.utils.paths import UnsafePathError, safe_join
from petkit_local.web.api._common import (
    MAX_CAPTURE_LIMIT, MAX_EVENT_LIMIT, _device_log_reason, _device_log_root, _limit_param,
)
from petkit_local.web.api.settings import _current_settings

log = logging.getLogger(__name__)


# Device-log browser caps. The files come off an unauthenticated listener, so
# the line cap bounds the response and the character cap stops one pathological
# line (a firmware hexdump, a runaway loop) from being the whole payload.
MAX_LOG_LINES = 2000
MAX_LOG_LINE_CHARS = 2000
MAX_LOG_FILES = 200


async def api_events(request: web.Request) -> web.Response:
    """A bare JSON array of the most recent hub events, oldest first.

    Optionally narrowed to one `?device=`. This backfills the Log tab on load;
    `api_ws` is what keeps it live afterwards.
    """
    hub = request.app["hub"]
    limit = _limit_param(request, 200, MAX_EVENT_LIMIT)
    device = request.query.get("device")
    did = to_int(device, None) if device else None
    return web.json_response(hub.recent(limit, device_id=did))


async def api_ws(request: web.Request) -> web.WebSocketResponse:
    """Stream hub events to the panel: a replay of the last 80, then live ones.

    A `{"kind": "ping"}` frame goes out whenever the queue is idle for 25s. It
    is not a keepalive (aiohttp's own `heartbeat` covers that) — it is what
    tells the frontend the connection is still good, and its absence is what
    flips the header pill to disconnected.
    """
    hub = request.app["hub"]
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    q = hub.subscribe()
    try:
        for ev in hub.recent(80):
            await ws.send_json(ev)
        while not ws.closed:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=25)
                await ws.send_json(ev)
            except asyncio.TimeoutError:
                await ws.send_json({"kind": "ping", "ts": time.time()})
    except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
        pass
    finally:
        hub.unsubscribe(q)
    return ws


def _capture_dir(request: web.Request) -> str:
    """The configured capture directory, or an empty string when there is none."""
    return request.app["cfg"].get("capture_dir", "")


async def api_capture_list(request: web.Request) -> web.Response:
    """List the capture files as `{"dir", "files": [{name, size, lines}], "enabled"}`.

    A file that cannot be read is skipped rather than failing the listing —
    a capture in progress is being appended to underneath us.

    Counting the lines means reading every capture file end to end, and nothing
    prunes them (`api_capture_delete`), so the whole scan goes to a thread: this
    process also serves the devices, and blocking here stalls their HTTP server
    and the MQTT bridge with it.
    """
    d = _capture_dir(request)
    files = await asyncio.to_thread(_capture_listing, d) if d else []
    return web.json_response({"dir": d, "files": files, "enabled": _current_settings(request)["capture"]})


def _capture_listing(d: str) -> list[dict[str, Any]]:
    """Name, size and line count of every capture file in `d`."""
    files: list[dict[str, Any]] = []
    if not os.path.isdir(d):
        return files
    for name in sorted(os.listdir(d)):
        if name.endswith(".jsonl"):
            p = os.path.join(d, name)
            try:
                with open(p) as f:
                    lines = sum(1 for _ in f)
                files.append({"name": name, "size": os.path.getsize(p), "lines": lines})
            except OSError:
                pass
    return files


def _safe_capture_path(request: web.Request) -> str | None:
    """Resolve the `{name}` route part inside the capture directory, or None.

    Containment is `safe_join`'s job. The extension check stays because it is a
    contract of this endpoint, not a safety measure: the capture directory is
    listed as `*.jsonl` only, and both readers below assume JSON lines.
    """
    d = _capture_dir(request)
    name = request.match_info["name"]
    if not d or not name.endswith(".jsonl"):
        return None
    try:
        p = safe_join(d, name)
    except UnsafePathError:
        return None
    return p if os.path.isfile(p) else None


async def api_capture_read(request: web.Request) -> web.Response:
    """Answer `{"records": [...], "total": n}` for one capture file.

    `records` is the TAIL of the file — the newest `?limit=` lines — while
    `total` counts every line, so the UI can say "showing 100 of 40000". A line
    that is not valid JSON is returned as `{"raw": line}` rather than dropped:
    a malformed record is usually the one being looked for.
    """
    p = _safe_capture_path(request)
    if not p:
        return web.json_response({"error": "not found"}, status=404)
    limit = _limit_param(request, 100, MAX_CAPTURE_LIMIT)
    # Only the tail is kept in memory — a capture file grows unbounded while
    # capture mode is on, and reading all of it just to slice the end of it made
    # the response cost scale with the file, not with the request.
    tail: deque[str] = deque(maxlen=limit)
    total = 0
    try:
        with open(p) as f:
            for line in f:
                total += 1
                tail.append(line)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    out: list[dict[str, Any]] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"raw": line})
    return web.json_response({"records": out, "total": total})


async def api_capture_download(request: web.Request) -> web.StreamResponse:
    """Serve a capture file as an attachment, for offline analysis."""
    p = _safe_capture_path(request)
    if not p:
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(p, headers={
        "Content-Disposition": f"attachment; filename=\"{quote(os.path.basename(p))}\"",
    })


async def api_capture_delete(request: web.Request) -> web.Response:
    """Delete one capture file; answers `{"ok": True, "name": ...}`.

    Capture files grow without bound while capture mode is on and nothing prunes
    them — unlike media and device logs, they have no retention sweep, because
    a capture is something you turn on deliberately and want in full. So the way
    to reclaim the space is to delete a file once you have what you needed from
    it.

    The name goes through `_safe_capture_path`, the same containment check the
    read and download endpoints use, so this cannot reach outside the capture
    directory or touch anything that is not a `.jsonl`. A file the device is
    still appending to can be deleted: the writer holds an open descriptor and
    keeps writing to the now-unlinked inode until it rotates, which loses only
    what has not been written yet.
    """
    p = _safe_capture_path(request)
    if not p:
        return web.json_response({"error": "not found"}, status=404)
    name = os.path.basename(p)
    try:
        await asyncio.to_thread(os.unlink, p)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    log.info("panel: deleted capture file %s", name)
    return web.json_response({"ok": True, "name": name})


def _device_log_listing(root: str) -> list[dict[str, Any]]:
    """Every uploaded log file under `root`, newest first.

    Walks a tree an unauthenticated listener writes, so it runs in a thread for
    the same reason `api_device_log_read` does.
    """
    files: list[dict[str, Any]] = []
    if not os.path.isdir(root):
        return files
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
            except OSError:
                continue  # being written to, or vanished mid-walk
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            files.append({
                "rel": rel,
                "name": name,
                "device": to_int(rel.split("/")[0], None),
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


async def api_device_logs(request: web.Request) -> web.Response:
    """List the uploaded device logs, newest first.

    Answers `{"dir", "reason", "enabled_devices", "files": [...], "total_bytes"}`.
    There is no database table behind this: the file IS the record, and the
    directory our own `pathPrefix` created carries the device id — the same
    arrangement `api_capture_list` uses for captures.
    """
    root = _device_log_root(request)
    reg = request.app["registry"]
    files = await asyncio.to_thread(_device_log_listing, root) if root else []
    want = to_int(request.query.get("device"), None)
    if want is not None:
        files = [f for f in files if f["device"] == want]
    return web.json_response({
        "dir": root,
        "reason": _device_log_reason(request),
        "enabled_devices": [d.petkit_id for d in reg.all()
                            if d.config.get("log_upload_enabled", False)],
        "files": files[:MAX_LOG_FILES],
        "total_bytes": sum(f["size"] for f in files),
    })


def _grep(text: str, query: str) -> list[list[Any]]:
    """Filter `text` to `[line_number, line]` pairs matching every term.

    Case-insensitive substring over whitespace-separated terms, ANDed. Never a
    regular expression: the panel is served unauthenticated on the HTTPS port,
    and a caller-supplied pattern over a caller-supplied file is a denial of
    service with no upside for what is a grep over logcat output.

    Line numbers are the FILE's, so a filtered view still says where you are.
    """
    terms = [t.lower() for t in query.split()] if query else []
    out: list[list[Any]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if terms:
            low = line.lower()
            if not all(t in low for t in terms):
                continue
        out.append([n, line[:MAX_LOG_LINE_CHARS]])
    return out


def _read_device_log(path: str, query: str, limit: int, offset: int) -> dict[str, Any]:
    """Read, filter and window one log file. Blocking; call in a thread.

    Decoded with `errors="replace"`: the bytes came from an unauthenticated
    listener, and a log that is 99% readable is worth showing.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    matched = _grep(text, query)
    window = matched[offset:offset + limit]
    return {
        "lines": window,
        "total": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
        "matched": len(matched),
        "offset": offset,
        "limit": limit,
        "size": os.path.getsize(path),
    }


async def api_device_log_read(request: web.Request) -> web.StreamResponse:
    """One log's contents, filtered and windowed — or the raw file to download.

    Served unmasked, unlike `/api/blocked`: masking a log makes it useless, and
    this is the same exposure `/api/capture/{name}` already accepts on the same
    unauthenticated port. It is not a new trust level, it is an existing one
    extended — which is also why collection is off until switched on.
    """
    root = _device_log_root(request)
    if not root:
        return web.json_response({"error": "not found"}, status=404)
    try:
        path = safe_join(root, request.match_info["path"])
    except UnsafePathError:
        return web.json_response({"error": "not found"}, status=404)
    if not os.path.isfile(path):
        return web.json_response({"error": "not found"}, status=404)

    if request.query.get("download"):
        return web.FileResponse(path, headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{quote(os.path.basename(path))}"',
        })

    limit = _limit_param(request, 500, MAX_LOG_LINES)
    offset = max(0, to_int(request.query.get("offset"), 0) or 0)
    query = request.query.get("q", "")
    # The one panel endpoint whose work scales with a file an unauthenticated
    # listener wrote, so it does not run on the event loop.
    try:
        payload = await asyncio.to_thread(_read_device_log, path, query, limit, offset)
    except OSError as e:
        return web.json_response({"error": str(e)}, status=500)
    return web.json_response(payload)
