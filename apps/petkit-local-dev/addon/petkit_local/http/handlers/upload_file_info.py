"""dev_upload_file_info_v2 — what the device just uploaded, and what it was.

The `cloud` process PUTs a raw, AES-encrypted, cryptically named file to the
bucket (`http/bucket.py`'s hidden `.raw` staging dir) and only then calls this
endpoint to say what it is: `fileId`, `moduleType`, `encrypt`/`aesIv`, the
`eventId` it belongs to and the `petEvent`/`cleanEvent`/`cvrEvent`/`toiletEvent`
flags. Without this call the bucket holds undecryptable bytes nothing can name,
so this is the only place the two halves of a media item are joined.

Each entry gets an EventStore `media` row immediately — the timeline shows the
item while it is still being processed — plus a background `media/pipeline.py`
task that decrypts, remuxes and moves it into the friendly tree. The request
returns as soon as the rows are written; the device is waiting on it and ffmpeg
is not fast.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from petkit_local.events import ingest
from petkit_local.http.handlers._common import request_device
from petkit_local.media import pipeline
from petkit_local.utils.capture import capture_record

if TYPE_CHECKING:
    from petkit_local.devices.base import Device
    from petkit_local.events.store import EventStore

log = logging.getLogger(__name__)

# Strong references to the in-flight pipeline tasks. asyncio only keeps a WEAK
# reference to a running task, so a task nothing else holds can be garbage
# collected mid-await and silently stop halfway through decrypt/remux. The set
# is also what lets shutdown wait for them: the tasks write to the EventStore,
# and a write after `EventStore.close()` raises.
_pending: set[asyncio.Task[None]] = set()


def parse_file_infos(text: str) -> list[dict]:
    """Pull the `fileInfos` array out of a request body.

    The device POSTs form-urlencoded with a `fileInfos` field holding a JSON
    array — the same convention as `dev_state_report`'s `state=<JSON>` and
    `dev_event_report`'s fields — but the field is seen both already decoded and
    as a nested JSON string, and `snake_case` as well as `camelCase`.

    Returns:
        The list of per-file metadata dicts, or ``[]`` for anything unparseable.
        Entries are NOT validated here; `events/ingest.from_file_info` is what
        rejects a malformed one.
    """
    parsed = ingest.parse_event_report_form(text)
    if isinstance(parsed, list):
        infos = parsed
    elif isinstance(parsed, dict):
        infos = parsed.get("fileInfos", parsed.get("file_infos"))
    else:
        infos = None
    if isinstance(infos, str):
        try:
            infos = json.loads(infos)
        except (json.JSONDecodeError, TypeError):
            infos = None
    return infos if isinstance(infos, list) else []


def pending_count() -> int:
    """Number of media-processing tasks still in flight (for diagnostics)."""
    return len(_pending)


async def wait_for_pending(timeout: float = 30.0) -> None:
    """Let the outstanding media tasks finish, cancelling any stragglers.

    main.py must call this on shutdown BEFORE `event_store.close()`, otherwise
    a task still in its pipeline writes to a closed store.

    Args:
        timeout: Seconds to wait before cancelling what is left. Cancellation
            is awaited too, so this returns with nothing still running.
    """
    if not _pending:
        return
    _, still_running = await asyncio.wait(set(_pending), timeout=timeout)
    for task in still_running:
        task.cancel()
    if still_running:
        log.warning("Cancelled %d media pipeline task(s) still running at shutdown",
                    len(still_running))
        await asyncio.gather(*still_running, return_exceptions=True)


async def handle_upload_file_info(request: web.Request) -> web.Response:
    """Record what the device just uploaded, and start processing it.

    Split in two on purpose. The `media` row is written synchronously, so the
    timeline shows the item the moment the device reports it; decrypt, remux and
    the move into the friendly tree run as a background task, because they are
    slow (ffmpeg) and the device is waiting on this response. Batches are
    normal — a single event's chunks arrive many per request.

    Returns:
        ``{"result": "success"}`` unconditionally. An unknown device, or a build
        with no event store, drops the metadata silently rather than reporting
        an error, per the never-fail-a-device rule in `http/server.py`; the file
        itself is already in the bucket either way.
    """
    device = request_device(request)

    store = request.app.get("event_store")
    if not device or store is None:
        return web.json_response({"result": "success"})
    petkit_id = device.petkit_id

    raw = await request.read()
    infos = parse_file_infos(raw.decode("utf-8", "replace"))

    config = request.app["config"]
    if config.get("capture"):
        capture_record(config.get("capture_dir", "/data/capture"), "upload_file_info",
                        {"id": petkit_id, "infos": infos})

    hub = request.app.get("event_hub")
    ha_publisher = request.app.get("ha_publisher")
    pet_registry = request.app.get("pet_registry")

    for info in infos:
        if not isinstance(info, dict):
            continue
        try:
            row = ingest.from_file_info(device, info)
        except ValueError as e:
            log.warning("Bad file_info entry from device %d: %s", petkit_id, e)
            continue

        await store.upsert_media(row)
        if hub is not None:
            hub.publish("media", petkit_id,
                        f"file_info: {row['category'] or '?'} ({row['file_id']})")

        task = asyncio.create_task(
            _process(device, info, config, store, hub, ha_publisher, pet_registry))
        _pending.add(task)
        task.add_done_callback(_pending.discard)

    return web.json_response({"result": "success"})


async def _process(
    device: Device,
    info: dict,
    config: dict,
    store: EventStore,
    hub: Any,
    ha_publisher: Any,
    pet_registry: Any,
) -> None:
    """Run one file through the media pipeline, swallowing whatever it raises.

    The task body for `handle_upload_file_info`, one task per file. A failure is
    logged and dropped rather than re-raised: the files in a batch are
    independent, and one unreadable upload must not take the rest of the batch
    (or the shutdown drain in `wait_for_pending`) with it.

    Args:
        hub, ha_publisher, pet_registry: Optional collaborators, typed `Any`
            because each may legitimately be None (a bucket-only run, a build
            without HA) and the pipeline is what checks.
    """
    try:
        await pipeline.process_file_info(device, info, config, store, hub=hub,
                                         ha_publisher=ha_publisher, pet_registry=pet_registry)
    except asyncio.CancelledError:
        # Shutdown cancelled us; nothing to report, and re-raising keeps the
        # task's state as cancelled rather than "completed successfully".
        raise
    except Exception:
        # Nothing awaits this task, so an escaping exception would only surface
        # as asyncio's "exception was never retrieved" at GC time, if at all.
        log.exception("media pipeline failed for device %d file %s",
                     device.petkit_id, info.get("fileId") or info.get("file_id"))
