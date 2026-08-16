"""Thin ffmpeg wrapper — stream-copy remux of the device's raw `.ts` clips
into a browser/HA-media-player-friendly `.mp4`, and a codec probe for the
verification step. No re-encode (`-c copy`): lossless, fast, low CPU. Missing
ffmpeg degrades the feature (caller falls back to keeping the `.ts`) rather
than failing the request.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil

log = logging.getLogger(__name__)

_have_ffmpeg_cache: bool | None = None


def have_ffmpeg() -> bool:
    """Whether ffmpeg is on PATH, resolved once per process.

    Cached because it is checked on every single upload and the answer cannot
    change inside a container's lifetime.
    """
    global _have_ffmpeg_cache
    if _have_ffmpeg_cache is None:
        _have_ffmpeg_cache = shutil.which("ffmpeg") is not None
    return _have_ffmpeg_cache


#: Deliberately generous. These bound a HUNG process, they do not police a slow
#: one — a timeout that fires on legitimate work silently loses a recording,
#: which is worse than the hang it was meant to prevent.
PROBE_TIMEOUT = 60.0
#: A single frame extract.
THUMB_TIMEOUT = 120.0
#: A stream copy, so fast, but a long recording is still a big file.
REMUX_TIMEOUT = 600.0
#: Joins every ~4s chunk of one visit, so the largest budget of the four.
STITCH_TIMEOUT = 1800.0


async def run_ffmpeg(args: list[str], *, timeout: float, what: str,
                     stdin_data: bytes | None = None) -> tuple[int, bytes, bytes]:
    """Run one ffmpeg/ffprobe command with a deadline. Never raises.

    Args:
        args: The full argv, including the binary name.
        timeout: Seconds before the child is killed. See the constants above.
        what: Named in the log line, so a timeout says which file it was about.
        stdin_data: Fed to the child on stdin (the face-photo resize pipes the
            image in rather than staging a temp file).

    Returns:
        `(returncode, stdout, stderr)`. A returncode of -1 means it never ran
        (binary missing, fork failed); -2 means it hit the deadline and was
        killed. Both are failures the caller already handles as "non-zero".

    Killing the child is the whole point and is easy to get wrong:
    `asyncio.wait_for` cancels the *await*, not the process, so simply timing
    out would leave a wedged ffmpeg holding the file and its pipes forever —
    strictly worse than the hang being fixed. So the timeout path kills, then
    drains the pipes: a process killed without its output being read can block
    on a full pipe buffer instead of dying.

    `CancelledError` gets the same treatment, and that is not theoretical:
    the stitcher is cancelled at shutdown, so without it a restart mid-stitch
    orphans the child.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        log.warning("ffmpeg exec failed for %s: %s", what, e)
        return -1, b"", b""

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(stdin_data), timeout)
    except asyncio.TimeoutError:
        log.error("ffmpeg timed out after %.0fs for %s - killing it", timeout, what)
        await _kill(proc)
        return -2, b"", b""
    except asyncio.CancelledError:
        await _kill(proc)
        raise
    return proc.returncode or 0, stdout, stderr


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill a child and reap it, so no zombie and no held file handles."""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        return
    try:
        # Drains the pipes as well as reaping: a killed process whose output
        # was never read can block writing to a full pipe instead of exiting.
        await asyncio.wait_for(proc.communicate(), 10)
    except (asyncio.TimeoutError, Exception):
        log.warning("ffmpeg did not exit after kill (pid %s)", proc.pid)


async def remux_ts_to_mp4(src: str, dst: str) -> bool:
    """Stream-copy remux. Returns True on success; never raises."""
    if not have_ffmpeg():
        log.warning("ffmpeg not installed - cannot remux %s, keeping as .ts", src)
        return False
    rc, _, stderr = await run_ffmpeg(
        ["ffmpeg", "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", dst],
        timeout=REMUX_TIMEOUT, what=src)
    if rc != 0:
        log.warning("ffmpeg remux failed (%s): %s", src, stderr.decode(errors="replace")[-500:])
        return False
    return True


async def probe(src: str) -> dict:
    """ffprobe summary: per-stream codec_name/codec_type/width/height plus the
    container's duration/size. Used for the encryption/codec verification step
    and by media/stitch.py, which needs `format.duration` to tell a complete
    join from one that silently dropped input, and the per-stream dimensions
    to tell joinable chunks from mismatched ones. Returns {} if ffprobe is
    unavailable or the file isn't valid media — never raises."""
    if not shutil.which("ffprobe"):
        return {}
    rc, stdout, _ = await run_ffmpeg(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,codec_type,width,height:format=duration,size",
         "-of", "json", src],
        timeout=PROBE_TIMEOUT, what=src)
    if rc != 0:
        return {}
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


#: What the device's NPU is fed. Every reference photo PetKit's cloud served for
#: this household was exactly 224x224, and none was a plain snapshot — they are
#: pre-cropped face chips. So the app crops and downscales before upload, and
#: that is the input the recogniser was tuned against; handing it a 4000x3000
#: phone photo is not the same thing.
FACE_PHOTO_SIZE = 224

#: JPEG frame markers. SOF0..SOF15 carry the dimensions; the four excluded here
#: share the range but are not frame headers (DHT, JPG, DAC, and the restart
#: markers' neighbour).
_SOF_MARKERS = {*range(0xC0, 0xD0)} - {0xC4, 0xC8, 0xCC}


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a JPEG, or None if it cannot be read.

    Done here rather than by shelling out to ffprobe because the common case is
    a photo that is ALREADY the right size, and re-encoding one costs a
    generation of JPEG loss for nothing. Header-only, so it never decodes the
    image and never raises on the arbitrary bytes an upload may carry.
    """
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    end = len(data)
    while i + 3 < end:
        if data[i] != 0xFF:
            i += 1
            continue
        # A marker may be preceded by any number of 0xFF fill bytes, which are
        # legal padding. Treating one as the marker itself read a length out of
        # the real marker and lost the frame header entirely.
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in _SOF_MARKERS:
            if i + 9 > end:
                return None
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        if marker == 0xD9:      # end of image, before any frame header
            return None
        # Standalone markers: no length field follows them.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        segment = int.from_bytes(data[i + 2:i + 4], "big")
        if segment < 2:
            return None
        i += 2 + segment
    return None


async def normalize_face_photo(data: bytes, size: int = FACE_PHOTO_SIZE) -> bytes:
    """Centre-crop to square and scale to `size`x`size`, as the app does.

    Returned unchanged when the image is already square at `size` — which is
    what every photo captured from PetKit's cloud is — so re-uploading one of
    those is byte-identical rather than a second JPEG generation.

    Missing ffmpeg degrades the feature exactly as it does for video: the
    original is stored and the device gets a photo of the wrong size, which is
    worse recognition rather than none. Same on any conversion failure — an
    upload must not be lost because a filter graph did not like it.
    """
    dims = jpeg_dimensions(data)
    if dims == (size, size):
        return data
    if not have_ffmpeg():
        log.warning("ffmpeg not installed - storing a face photo at %s instead of %dx%d",
                    f"{dims[0]}x{dims[1]}" if dims else "an unknown size", size, size)
        return data

    rc, stdout, stderr = await run_ffmpeg(
        ["ffmpeg", "-y", "-i", "pipe:0",
         # Square from the centre first, then scale — cropping after the
         # scale would squash a non-square photo before choosing what to keep.
         "-vf", f"crop='min(iw,ih)':'min(iw,ih)',scale={size}:{size}:flags=lanczos",
         "-frames:v", "1", "-q:v", "2", "-f", "mjpeg", "pipe:1"],
        timeout=THUMB_TIMEOUT, what="face photo resize", stdin_data=data)
    if rc != 0 or not stdout.startswith(b"\xff\xd8"):
        log.warning("could not resize a face photo, storing it as uploaded: %s",
                    stderr.decode(errors="replace")[-300:])
        return data
    return stdout
