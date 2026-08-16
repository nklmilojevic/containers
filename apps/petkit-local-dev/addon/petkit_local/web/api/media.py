"""Serving the friendly media tree, and deciding what is ready to play.

Two jobs. The routes hand out one file or one thumbnail, with every path built
from request input going through `utils/paths.safe_join` and every rejection
answering 404. The rest is the role -> slot mapping the Timeline renders from:
which of an episode's uploads is the playable recording, which is the poster,
and whether a chunked video has been stitched yet or is still assembling.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from typing import Any

from aiohttp import web

from petkit_local.media.stitch import QUIET_SECONDS as _STITCH_QUIET
from petkit_local.media.transcode import THUMB_TIMEOUT, have_ffmpeg, run_ffmpeg
from petkit_local.utils.paths import UnsafePathError, safe_join

log = logging.getLogger(__name__)


def _safe_media_path(request: web.Request, rel_path: str) -> str | None:
    """Resolve `rel_path` under the friendly media root, or None.

    Containment (`..`, absolute paths, symlink escapes) is `safe_join`'s job;
    what stays here is this endpoint's own contract: an unconfigured media root
    serves nothing (`safe_join` would happily resolve against the process CWD),
    the result must be an existing regular file, and a rejection is a 404 rather
    than an exception.
    """
    media_root = request.app["cfg"].get("media_root", "")
    if not media_root or not rel_path:
        return None
    try:
        candidate = safe_join(media_root, rel_path)
    except UnsafePathError:
        return None
    return candidate if os.path.isfile(candidate) else None


async def api_media_file(request: web.Request) -> web.StreamResponse:
    """Serve one file from the friendly media tree.

    The path is relative to the media root — the same form the timeline hands
    out. 404 covers both "missing" and "rejected", so a probe cannot tell the
    two apart.
    """
    p = _safe_media_path(request, request.match_info["path"])
    if not p:
        return web.json_response({"error": "not found"}, status=404)
    return web.FileResponse(p)


async def _generate_video_thumb(video_path: str, thumb_path: str) -> bool:
    """Grab one frame from `video_path` into `thumb_path`; True if it worked.

    ffmpeg writes to a temp file in the destination directory which is then
    renamed into place, so `thumb_path` only ever exists complete. The Timeline
    renders a whole day at once, so several requests for the same not-yet-made
    thumbnail arrive together; letting them all write the final path directly
    means one request can serve a half-written JPEG. Renaming is preferred over
    a per-path asyncio.Lock because it needs no shared state to keep correct
    (the runs are equivalent, so last-writer-wins is fine) and it also survives
    the process being killed mid-encode.
    """
    # The temp file must share a filesystem with the target for os.replace to
    # be atomic, so it goes in the same directory.
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(thumb_path) or ".",
                                    prefix=".thumb.", suffix=".jpg")
    os.close(fd)  # ffmpeg opens the path itself; the extension picks the muxer
    try:
        rc, _, _ = await run_ffmpeg(
            ["ffmpeg", "-y", "-i", video_path, "-frames:v", "1", "-vf", "thumbnail", tmp_path],
            timeout=THUMB_TIMEOUT, what=video_path)
        if rc != 0 or not os.path.getsize(tmp_path):
            return False
        os.replace(tmp_path, thumb_path)
        return True
    except OSError as e:
        log.warning("panel: thumbnail generation failed for %s: %s", video_path, e)
        return False
    finally:
        # os.replace consumed the temp file on success; on every other path
        # (including cancellation) it is still there.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def api_media_thumb(request: web.Request) -> web.StreamResponse:
    """Serve a thumbnail for a media path: the image itself for stills, or a
    cached frame grab for video.

    Grabs are cached under `{data_dir}/thumbs` keyed by a hash of the source
    path, so the Timeline re-rendering a day costs one ffmpeg run per clip
    total, not per request. Every "no thumbnail" outcome answers 404, including
    a failed grab: that is the only status the UI has a fallback for
    (`hide-on-error`), and a 500 leaves a broken tile where the poster still
    would have gone.
    """
    p = _safe_media_path(request, request.match_info["path"])
    if not p:
        return web.json_response({"error": "not found"}, status=404)

    if p.lower().endswith((".jpg", ".jpeg", ".png")):
        return web.FileResponse(p)  # the browser downsizes via CSS — no separate pipeline needed

    if not have_ffmpeg():
        return web.json_response({"error": "ffmpeg not available for video thumbnails"}, status=404)

    data_dir = request.app["cfg"].get("data_dir", "/data")
    thumbs_dir = os.path.join(data_dir, "thumbs")
    thumb_path = os.path.join(thumbs_dir, hashlib.sha1(p.encode()).hexdigest() + ".jpg")

    if not os.path.isfile(thumb_path):
        os.makedirs(thumbs_dir, exist_ok=True)
        if not await _generate_video_thumb(p, thumb_path):
            return web.json_response({"error": "thumbnail generation failed"}, status=404)

    return web.FileResponse(thumb_path)


def _rel_media(path: str | None, media_root: str) -> str | None:
    """An absolute stored media path as the media-root-relative URL part."""
    if not path:
        return None
    try:
        return os.path.relpath(path, media_root)
    except ValueError:
        # Different drives on Windows — no relative form exists.
        return None


def _pick_chunked_video(rows: list[dict[str, Any]], media_root: str,
                        now: float) -> tuple[str | None, bool]:
    """`(url, pending)` for a CHUNKED video role (fullVideo / cloudDouble).

    A real T5 uploads these as many ~4s chunks that media/stitch.py joins into
    one clip once the episode goes quiet. Until that's done we must NOT hand
    the UI a raw chunk — playing a 4-second fragment of a visit is exactly the
    rough edge to avoid. So a chunked video is "ready" only when the stitched
    result exists, or a lone chunk has clearly settled (the stitcher looked
    and left it because there was nothing to join). Otherwise it's `pending`
    and the UI shows a still / "processing" state instead."""
    ready = [m for m in rows if m.get("status") == "ready" and m.get("media_path")]
    stitched = next((m for m in ready if m.get("stitch_state") == "stitched"), None)
    if stitched:
        return _rel_media(stitched["media_path"], media_root), False
    expecting = bool(rows)  # any row (even pending) means a recording is coming
    if len(ready) == 1 and not any(m.get("status") == "pending" for m in rows):
        newest = ready[0].get("created_at") or 0
        if now - newest > _STITCH_QUIET:
            return _rel_media(ready[0]["media_path"], media_root), False
    return None, expecting


def _pick_single_video(rows: list[dict[str, Any]], media_root: str) -> str | None:
    """A SINGLE-file clip role (dynamicVideo / highLight = the app's short
    'Highlight'): the device uploads it as one complete ~4s file, not chunks,
    so it's ready the moment it's processed — no stitching, never a fragment."""
    for m in rows:
        if m.get("status") == "ready" and m.get("media_path"):
            r = _rel_media(m["media_path"], media_root)
            if r:
                return r
    return None


def _session_media_urls(sessions_media: list[dict[str, Any]], media_root: str,
                        now: float | None = None) -> dict[str, Any]:
    """Media slots for one episode, keyed by role (see events/ingest.py).

    - `highlight_url` — the short ~4s event clip (`dynamicVideo`); ready at once.
    - `playback_url`  — the long continuous recording (`fullVideo`); ready only
      once media/stitch.py has joined its chunks (else `video_pending`).
    - `preview_url`   — the `cloudDouble` timelapse; same chunked/stitched gate.
    - `poster_url` / `waste` / `health` — stills.
    - `snapshot_url`  — best still to use as a poster/thumbnail fallback.
    - `video_pending` — a playable recording is expected but still assembling,
      so the UI shows a still or a "processing" placeholder, not a fragment.
    """
    now = now if now is not None else time.time()
    by: dict[str | None, list[dict[str, Any]]] = {}
    for m in sessions_media:
        by.setdefault(m.get("category"), []).append(m)

    playback_url, pb_pending = _pick_chunked_video(by.get("fullVideo", []), media_root, now)
    preview_url, _pv_pending = _pick_chunked_video(by.get("cloudDouble", []), media_root, now)
    highlight_url = _pick_single_video(by.get("dynamicVideo", []) + by.get("highLight", []), media_root)
    poster_url = _pick_single_video(by.get("eventImage", []), media_root)

    def _ready_rels(cat: str) -> list[str]:
        """Every ready file of one still-image role, as relative URLs."""
        return [r for r in (_rel_media(m["media_path"], media_root)
                            for m in by.get(cat, [])
                            if m.get("status") == "ready" and m.get("media_path")) if r]

    out: dict[str, Any] = {
        "highlight_url": highlight_url, "playback_url": playback_url,
        "preview_url": preview_url, "poster_url": poster_url,
        "waste": _ready_rels("wasteCheck"), "health": _ready_rels("healthPic"),
        "snapshot_url": None, "video_pending": bool(pb_pending),
    }

    # Prefer the purpose-made poster, then a waste shot, as the thumbnail.
    out["snapshot_url"] = out["poster_url"] or (out["waste"][0] if out["waste"] else None)
    return out


def _has_media(slots: dict[str, Any]) -> bool:
    """True if a `_session_media_urls` result is worth rendering at all.

    `video_pending` counts: the placeholder is the point of that flag.
    """
    return bool(slots.get("playback_url") or slots.get("highlight_url")
                or slots.get("waste") or slots.get("health")
                or slots.get("snapshot_url") or slots.get("preview_url")
                or slots.get("video_pending"))
