"""dev_discern_pic / dev_discern_config — on-device facial recognition support.

Which devices these apply to is decided by `Device.supports_ai`: a seed list of
codenames, widened the first time a device actually ASKS for either endpoint —
firmware without an NPU never does, and one codename can cover two generations
where only the newer has one. We do not run
recognition ourselves: this module hosts the reference face photos at
`/faces/{filename}` and tells the device where they are, and the device's own
NPU does the matching. The result comes back as `content.score_info[].id` in a
later event — that id is our own `pets` primary key, copied verbatim by the
firmware from the list entry we served here, so no id-mapping protocol exists.

Both bodies in this module follow the real PetKit cloud, captured through proxy
mode on 2026-07-27. What they returned before did not, which is why nothing was
ever attributed to a pet.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from aiohttp import web

from petkit_local.http.handlers._common import no_device_response, request_device
from petkit_local.utils.coerce import to_bool, to_float, to_int
from petkit_local.utils.paths import UnsafePathError, safe_join

log = logging.getLogger(__name__)

#: The thresholds PetKit's cloud serves a T5. Used unless a device overrides
#: them in `config["ai_thresholds"]`. See `handle_discern_config` for what they
#: actually gate — they are not cosmetic, and the device writes them to flash.
DEFAULT_DISCERN_AREA = 6000
DEFAULT_DISCERN_SCORE = 25.0


def _ai_on(device) -> bool:
    """Whether this device should be asked to recognise anyone.

    Coerced rather than `bool()`-ed: the flag round-trips through devices.json,
    where a stray `"false"` string would otherwise read as enabled.
    """
    return bool(device) and device.supports_ai and to_bool(
        device.config.get("ai_enabled", True), True)


def _note_ai_capable(request: web.Request, device) -> None:
    """Remember that this device asked for facial-recognition data.

    Asking IS the capability: firmware without an NPU has no reason to poll
    these endpoints, and one with an NPU asks for `dev_discern_config` on every
    boot and roughly hourly after that.
    That is the only reliable signal we have, because a codename cannot answer
    it — PetKit ships two generations of YumShare under one codename and only
    the newer recognises anything (`utils/const.py::DEVICE_TYPES_AI`).

    Gated on `is_camera`, which is not a guess but a definition: recognition
    needs something to see with. Without it a stray poll — or a request forged
    by anything that can reach this LAN port — would mark an ESP32 litter box or
    a pump-only fountain as AI-capable, and the panel would then offer face
    photos to hardware that has no camera.

    Written once, on the transition, so a device polling hourly forever does not
    keep marking the registry dirty. Debounced like every other config write;
    losing it to a hard kill costs one poll, not correctness.
    """
    if device is None or not device.is_camera or device.config.get("ai_observed"):
        return
    device.config["ai_observed"] = True
    registry = request.app.get("registry")
    if registry is not None:
        registry.mark_dirty()
    log.info("Device %s (%s) asked for facial-recognition data — marking it AI-capable",
             device.petkit_id, device.device_type)


async def handle_discern_pic(request: web.Request) -> web.Response:
    """Hand the device the reference face photos it should recognise pets by.

    The URLs are built against the host in `api_url` rather than the request's
    own Host header where possible: the device fetches them in a separate
    connection, so they have to name an address it can reach on its own, not
    whatever it happened to dial us on.

    This is also where `ai_enabled` is enforced. `dev_discern_config` carries no
    on/off field on the wire, so "recognise nobody" can only be said by serving
    an empty list here.

    Returns:
        `PetRegistry.discern_pic_payload()`'s dict, or ``{"result": {"list": []}}``
        for an unknown device, a device without NPU support, one with AI turned
        off, or a build with no pet registry.
    """
    device = request_device(request)
    pet_registry = request.app.get("pet_registry")
    # Before the gate, on purpose: the whole point is to learn about a device
    # the seed list does not name, and that device fails the gate today.
    _note_ai_capable(request, device)

    if pet_registry is None or not _ai_on(device):
        return web.json_response({"result": {"list": []}})

    config = request.app["config"]
    api_host = urlparse(config.get("api_url", "")).netloc or request.host
    return web.json_response(await pet_registry.discern_pic_payload(device.petkit_id, api_host))


async def handle_discern_config(request: web.Request) -> web.Response:
    """Give the device the two thresholds its detector runs on.

    Returns ``{"result": {"list": {"area": int, "score": float}}}`` — the real
    cloud's shape, captured 2026-07-27. This endpoint carries NO enable flag.
    `aiAnalyse` and `discernPic` are STATE-report field names (device -> us) and
    the firmware never looks for them here, so `ai_enabled` expresses itself
    through `dev_discern_pic` instead: no photos served, nobody to recognise.

    Both values are read by `get_pet_discern_config_by_network` as `valueint`
    (so the cloud's `25.0` truncates to `25`) and PERSISTED TO DEVICE FLASH as
    `user.conf` `other.minArea` — which is why detection kept working while
    this endpoint was wrong: the device was still running on values PetKit's
    cloud had written there.

    `area` is a floor on the detected animal's bounding box in detector pixels
    (`detected_area >= threshold`); nothing below 6000 was ever reported across
    58 captured detections. `score` is the BODY-detection floor that decides
    whether a visit episode opens at all — it is NOT comparable with
    `content.score_info[].score`, which is a face-match similarity on a wholly
    different scale (values to 1846 against this threshold of 25). Confusing
    the two is exactly how this endpoint was misread; see
    `events/normalize.py::_extract_pet_ref`.
    """
    device = request_device(request)
    _note_ai_capable(request, device)

    if not device or not device.supports_ai:
        return no_device_response()

    # Served even when `ai_enabled` is off: these gate episode creation, not
    # just identification, so withholding them would suppress visits too.
    #
    # Type-checked, not merely truthiness-checked: `config` round-trips through
    # a hand-editable devices.json, and a list there would turn `.get` into an
    # AttributeError — a 500 to a device, which this server never sends.
    thresholds = device.config.get("ai_thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
    return web.json_response({"result": {"list": {
        "area": to_int(thresholds.get("area"), DEFAULT_DISCERN_AREA),
        "score": to_float(thresholds.get("score"), DEFAULT_DISCERN_SCORE),
    }}})


async def handle_faces(request: web.Request) -> web.Response:
    """Serve a pet's face photo to the device.

    The other half of `handle_discern_pic`: this is where the URLs it hands out
    actually resolve. Filenames are pre-sanitized at save time
    (ai/pets.py::_safe_photo_filename); traversal is still rejected here, because
    this handler is reachable by anything that can open a socket to us and must
    not depend on the write path having been careful.
    """
    data_dir = request.app["config"].get("data_dir", "/data")
    # The faces dir — not data_dir — is the root, so neither a traversal nor a
    # symlink planted inside it can reach the rest of /data (devices.json, the
    # bucket AES key, the event store).
    faces_dir = os.path.join(data_dir, "faces")
    try:
        path = safe_join(faces_dir, request.match_info["filename"])
    except UnsafePathError:
        return web.Response(status=400, text="bad filename")

    if not os.path.isfile(path):
        return web.Response(status=404, text="not found")
    return web.FileResponse(path)
