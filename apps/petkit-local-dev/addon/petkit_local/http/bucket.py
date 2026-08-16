"""Minimal S3/OSS-compatible bucket for the device's media uploads.

The device's `cloud` process gets STS credentials from
`dev_oss_sts_info_new_v2` and uses them to upload photos, event clips and logs.
This listener stands in for Aliyun OSS: it accepts everything regardless of auth
(the credentials are ours anyway) and writes it to `/media/petkit`, from where
`media/pipeline.py` turns it into something the HA media browser can play.

Because there is no auth and the listener binds 0.0.0.0, the object key is
fully untrusted input: every handler resolves it through `safe_join`, and
nothing else in this module is allowed to build a path from it.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import TYPE_CHECKING

from aiohttp import web

from petkit_local.utils.const import DEVICE_LOG_KEY_PREFIX
from petkit_local.utils.jsonio import atomic_write_bytes
from petkit_local.utils.paths import UnsafePathError, safe_join

if TYPE_CHECKING:
    from petkit_local.devices.registry import DeviceRegistry
    from petkit_local.web.hub import EventHub

log = logging.getLogger(__name__)


def create_bucket_app(media_root: str, hub: "EventHub | None" = None,
                      log_root: str | None = None,
                      registry: "DeviceRegistry | None" = None,
                      data_dir: str = "/data") -> web.Application:
    """Build the OSS-compatible upload app the device's uploaders talk to.

    Args:
        media_root: The hidden RAW staging dir (e.g. /media/petkit/.raw) — files
            land here verbatim, encrypted, cryptically named; media/pipeline.py
            decrypts/remuxes/renames them into the friendly tree once
            dev_upload_file_info_v2 describes what they are.
        hub: Panel event hub, notified of each raw upload. Optional so the app
            can be built standalone (tests, a bucket-only run).
        log_root: Where a device's own debug log is stored (see `_route`). Kept
            OUT of the media tree deliberately: `media/pipeline.py` locates a
            raw file by substring match on `fileId` and deletes what it finds,
            so a `.log` sitting in `.raw` could be consumed by a media job.
            Omitted, no device log is accepted at all.
        registry: Used only to check whether the device named in a log key has
            collection switched on. Omitted, that check is skipped.

    Returns:
        An app serving PUT/POST/GET/HEAD on every path, with a 50 MB body cap.
        The cap matters because the endpoint is unauthenticated and each request
        is read fully into memory, so it is the only bound on what a single PUT
        can allocate.
    """
    app = web.Application(client_max_size=50 * 1024 * 1024)  # 50MB
    app["media_root"] = media_root
    app["log_root"] = log_root
    app["registry"] = registry
    app["hub"] = hub
    app["data_dir"] = data_dir

    app.router.add_route("GET", "/sounds/{device_id}/{filename}", handle_sound_file)
    app.router.add_route("PUT", "/{path:.*}", handle_put)
    app.router.add_route("POST", "/{path:.*}", handle_post)
    app.router.add_route("GET", "/{path:.*}", handle_get)
    app.router.add_route("HEAD", "/{path:.*}", handle_head)

    return app


def _key_parts(path: str) -> list[str]:
    """Split an object key the way `safe_join` will.

    Backslashes count as separators and empty/`.` segments are dropped, exactly
    as in `utils/paths.safe_join` — this has to agree with it, or a key could be
    routed by one shape and then joined by another. `..` is deliberately NOT
    dropped: `safe_join` lets it resolve and catches it with the containment
    check, which is the only test that also covers symlinks.
    """
    return [seg for seg in path.replace("\\", "/").split("/") if seg not in ("", ".")]


def _route(app: web.Application, path: str) -> tuple[str, str, list[str]] | None:
    """Decide which root an object key is stored under.

    The bucket serves two unrelated things on one unauthenticated listener: the
    media the `cloud` process uploads, and — when a device has log collection
    switched on — the debug log `logUpload` PUTs. They are told apart by the key
    prefix we ourselves handed out in the STS response, so the routing here and
    the `pathPrefix` in `devices/payloads.py` are two ends of one agreement.

    Returns:
        `(kind, root, rel_parts)` where `kind` is "media" or "log" and
        `rel_parts` is what gets joined under `root`, or None when a device-log
        key cannot be accepted — no log root configured, an unattributable key,
        or a device that has not been switched on. The caller answers 403 for
        None, which is terminal; a 5xx would make the firmware retry forever.
    """
    parts = _key_parts(path)
    if not parts or parts[0] != DEVICE_LOG_KEY_PREFIX:
        return "media", app["media_root"], parts

    log_root = app.get("log_root")
    if not log_root or len(parts) < 2:
        return None

    # Collection is per device, so the switch has to be checked against the id
    # in the key rather than a global flag. Without this the token is the only
    # gate, and a device switched off after being handed one would keep landing
    # logs here until its token expired.
    registry = app.get("registry")
    if registry is not None:
        try:
            device = registry.get(int(parts[1]))
        except ValueError:
            return None
        if device is None or not device.config.get("log_upload_enabled", False):
            return None

    return "log", log_root, parts[1:]


def _notify(request: web.Request, path: str, size: int) -> None:
    """Emit a live-log event for the panel, attributed to the uploading device.

    The object key carries the attribution, but at a different offset per kind,
    so this asks `_route` rather than assuming one: a media key is
    ``{device_type}/{petkit_id}/{cycleType}/{key}`` (see
    `devices/payloads.py::to_oss_sts`'s per-capability `pathPrefix`) and a
    device-log key is ``devlog/{petkit_id}/{name}``. Both put the id first in
    what `_route` returns as the remainder. A key without a usable one is
    published with no device — the upload is still worth showing, and this is a
    log line, not a data path.
    """
    hub = request.app.get("hub")
    if hub is None:
        return
    route = _route(request.app, path)
    kind, rest = (route[0], route[2]) if route else ("media", _key_parts(path))
    # A media key still carries its codename, so its id is the SECOND segment;
    # `_route` has already stripped the log prefix, putting the id first.
    offset = 0 if kind == "log" else 1
    device_id = None
    if len(rest) > offset:
        try:
            device_id = int(rest[offset])
        except ValueError:
            device_id = None
    if kind == "log":
        hub.publish("devlog", device_id, f"device log received: {path} ({size} bytes)")
    else:
        hub.publish("media", device_id, f"raw upload: {path} ({size} bytes)")


def _resolve_key(request: web.Request, *, for_write: bool = False) -> str | None:
    """Resolve the request's object key to an absolute path under media_root.

    Args:
        for_write: also refuse a key that resolves to the media root itself.
            `safe_join` counts the root as contained — correct for a read, but
            a write there is both impossible and unsafe, see below.

    Returns:
        The destination path, or None if the key escapes the root or (when
        writing) names the root — the caller must then answer with `_denied()`
        and touch no filesystem.
    """
    path = request.match_info["path"]
    route = _route(request.app, path)
    if route is None:
        log.warning("BUCKET %s: rejected key %r (device logs not accepted)",
                    request.method, path)
        return None
    _kind, root, rel_parts = route

    try:
        # safe_join already treats an absolute-looking key as relative, which
        # this endpoint needs: the device legitimately PUTs keys with a leading
        # slash. No lstrip of our own, or the containment check sees a
        # different string than the one we open.
        dest = safe_join(root, *rel_parts)
    except UnsafePathError as e:
        log.warning("BUCKET %s: rejected key %r (%s)", request.method, path, e)
        return None

    # An empty key -- `PUT /`, `PUT /.`, `PUT //` -- resolves to the root. That
    # write can never succeed, but refusing it here rather than letting it fail
    # is a containment matter, not tidiness: atomic_write_bytes stages its temp
    # file in the *parent* of its target, so a root-valued dest would put up to
    # client_max_size bytes of unauthenticated request body OUTSIDE the media
    # root (in /media/petkit, next to .raw) before the rename failed. For every
    # other key dest is strictly below the root, so its parent is too.
    # Compared against the root this key actually routed to, never a fixed one:
    # with two roots, checking only the media root would let `PUT /devlog/`
    # resolve to the log root, pass, and stage up to client_max_size bytes in
    # /data next to petkit.db.
    if for_write and dest == os.path.realpath(root):
        log.warning("BUCKET %s: rejected key %r (names the bucket root)", request.method, path)
        return None

    return dest


def _denied() -> web.Response:
    """Response for an object key that resolved outside the media root.

    403 rather than 400/500 on purpose: the device retries 5xx, so a server
    error would make a malformed key loop forever, while OSS answers AccessDenied
    for a key the credentials may not touch — a terminal answer the cloud
    process already knows how to give up on.
    """
    return web.Response(status=403, text="AccessDenied")


def _store_upload_sync(dest: str, body: bytes) -> str:
    """Write `body` to `dest` and return its ETag. Blocking; call in a thread."""
    atomic_write_bytes(dest, body)
    # Not a security digest, a cache validator — and MD5 specifically because a
    # real OSS ETag *is* the uppercase MD5 hex of the object, so a device that
    # verifies the upload against its own checksum still matches. usedforsecurity
    # keeps this working on FIPS-restricted builds of OpenSSL.
    return '"%s"' % hashlib.md5(body, usedforsecurity=False).hexdigest().upper()


async def _store_upload(dest: str, body: bytes) -> str | None:
    """Persist an upload off the event loop.

    The write and the digest both run in the worker thread: a body is up to
    50 MB (client_max_size), and hashing or fsyncing that on the single shared
    loop stalls MQTT, the device API and the panel for the duration.

    Returns:
        The ETag header value, quotes included, or None if the write could not
        happen at all.

    Every `OSError` is caught, not a hand-picked few. The obvious cases are
    keys no write can ever replace — a trailing slash (`t5/1/fullVideo/`) or a
    key that is a prefix of one already stored, both of which the device
    produces on its own — but the one that matters is **ENOSPC**: per `_denied`
    a 5xx makes the cloud process retry the same key forever, so a full disk
    escaping as a 500 becomes a permanent upload storm from every device at
    once. `FileExistsError` is in scope too — that is what
    `Path.mkdir(exist_ok=True)` raises when a parent path component is an
    existing regular file.

    A refusal is the right answer for all of them: the device stops asking for
    that key, and the operator sees the log line.
    """
    try:
        return await asyncio.to_thread(_store_upload_sync, dest, body)
    except OSError as e:
        log.warning("Bucket could not store %s: %s", dest, e)
        return None


async def handle_put(request: web.Request) -> web.Response:
    """Store an object — the upload path the device's `cloud` process uses.

    Answers with the `ETag` and `x-oss-request-id` headers a real OSS PUT
    returns, because the cloud process checks for them; the body is empty, as
    OSS's is. A key it cannot store (outside the root, the root itself, or an
    existing directory) gets `_denied()` and nothing is written.
    """
    # Key first, body second: a rejected key should not cost us up to 50 MB of
    # buffering for a request we are going to throw away.
    dest = _resolve_key(request, for_write=True)
    if dest is None:
        return _denied()

    path = request.match_info["path"]
    body = await request.read()
    content_type = request.content_type or "application/octet-stream"

    etag = await _store_upload(dest, body)
    if etag is None:
        log.warning("BUCKET PUT /%s: key names an existing directory", path)
        return _denied()

    log.info("BUCKET PUT /%s (%d bytes, %s) -> %s", path, len(body), content_type, dest)
    _notify(request, path, len(body))

    return web.Response(
        status=200,
        headers={
            "ETag": etag,
            "x-oss-request-id": f"petkit-local-{int(time.time())}",
        },
    )


async def handle_post(request: web.Request) -> web.Response:
    """Store an object uploaded via OSS's form-POST variant.

    Same storage path as `handle_put`, but the reply is OSS's `PostResponse`
    XML rather than an ETag header. An empty body is accepted and stores
    nothing, since a POST with no object is not an error to OSS.
    """
    dest = _resolve_key(request, for_write=True)
    if dest is None:
        return _denied()

    path = request.match_info["path"]
    body = await request.read()
    content_type = request.content_type or "application/octet-stream"

    log.info("BUCKET POST /%s (%d bytes, %s)", path, len(body), content_type)

    if body and await _store_upload(dest, body) is None:
        log.warning("BUCKET POST /%s: key names an existing directory", path)
        return _denied()

    return web.Response(
        status=200,
        headers={"x-oss-request-id": f"petkit-local-{int(time.time())}"},
        content_type="application/xml",
        text='<?xml version="1.0" encoding="UTF-8"?><PostResponse></PostResponse>',
    )


async def handle_get(request: web.Request) -> web.Response:
    """Serve a stored object back, or list the bucket when the key is empty.

    The device re-reads its own uploads (retry and verification paths), so this
    is not merely a debugging convenience. A missing object answers OSS's
    `NoSuchKey` 404 rather than an empty 200, which is what tells the cloud
    process the upload has to be repeated.
    """
    if not request.match_info["path"].strip("/"):
        return web.Response(
            status=200,
            content_type="application/xml",
            text='<?xml version="1.0" encoding="UTF-8"?>'
                 '<ListBucketResult><Name>petkit</Name></ListBucketResult>',
        )

    fpath = _resolve_key(request)
    if fpath is None:
        return _denied()
    if os.path.isfile(fpath):
        return web.FileResponse(fpath)

    return web.Response(status=404, text="NoSuchKey")


async def handle_head(request: web.Request) -> web.Response:
    """Report whether an object exists, and how large it is.

    This is the device's "did my upload land?" probe: 200 with `Content-Length`
    when it did, bare 404 when it did not. No body either way, per HEAD.
    """
    fpath = _resolve_key(request)
    if fpath is None:
        return _denied()

    if os.path.isfile(fpath):
        return web.Response(
            status=200,
            headers={"Content-Length": str(os.path.getsize(fpath))},
        )
    return web.Response(status=404)


async def handle_sound_file(request: web.Request) -> web.Response:
    """Serve an uploaded custom sound file for device download."""
    device_id = request.match_info["device_id"]
    filename = request.match_info["filename"]
    data_dir = request.app.get("data_dir", "/data")
    try:
        path = safe_join(
            safe_join(os.path.join(data_dir, "sounds"), device_id),
            filename,
        )
    except UnsafePathError:
        return web.Response(status=400, text="bad path")
    if os.path.isfile(path):
        return web.FileResponse(path)
    return web.Response(status=404, text="not found")
