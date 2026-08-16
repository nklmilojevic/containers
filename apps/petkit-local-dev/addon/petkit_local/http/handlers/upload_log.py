"""dev_upload_log_token / dev_upload_log — the device's own debug log.

The device asks for an upload token roughly every five minutes for as long as
it is running, and uploads only when something gives it a reason to. In 433
captured exchanges with the real cloud (2026-07-28) 428 polls produced 5
uploads, one of them tagged `uploadres=ctrl_reboot`.

The three steps, all confirmed against that capture and the `logUpload` binary:

1. ``GET dev_upload_log_token`` -> the STS block built by
   `payloads.to_log_upload_token`, whose docstring records the firmware
   constraints on its shape.
2. The device PUTs `/tmp/devRun.log` — plain text, `logcat -v time` with the
   agora and media debug logs appended, 12-234 KB observed — straight to the
   address in that block. `http/bucket.py` receives it; nothing in this module
   touches the bytes.
3. ``GET dev_upload_log?deviceId=&size=&key=&uploadres=`` reports the object it
   just wrote, and is answered with the bare string result below.

Collection is **per device** and off until switched on
(`config["log_upload_enabled"]`, from the panel). While it is off this module
answers exactly what the endpoints answered before it existed, so the disabled
path is a no-op rather than a new behaviour.

Two things make an upload fail silently, and both are worth knowing before
debugging one: `logUpload` validates TLS against `/app/bin/ca.crt`, so our
self-signed bucket needs the `cacert` patcher, and the bucket address has to be
splittable into a virtual-host authority (see `split_bucket_authority`). Neither
produces an error the device reports — the log simply never arrives.
"""
from __future__ import annotations

import logging

from aiohttp import web

from petkit_local.devices import payloads
from petkit_local.http.handlers._common import request_device
from petkit_local.utils.coerce import to_int

log = logging.getLogger(__name__)

#: What the cloud answers `dev_upload_log` with: a bare STRING result, not the
#: usual object (captured 2026-07-27). `http/redact/` sends the same literal.
UPLOAD_LOG_DONE = {"result": "success"}

#: "No token issued." The device's own log confirms it takes the hint rather
#: than retrying — "qiniu key or token is null, quit qiniu upload".
NO_TOKEN = {"result": {}}


async def handle_upload_log_token(request: web.Request) -> web.Response:
    """`dev_upload_log_token` — issue an upload token, or decline.

    Declines, with the empty result the device has always received here, when
    collection is off for this device, when the caller cannot be identified, or
    when the bucket address cannot be split into a virtual-host authority.

    An unidentified caller is declined rather than served from a throwaway
    Device the way `dev_oss_sts_info_new_v2` is (`handlers/stubs.py`): there
    would be no petkit id to build a `pathPrefix` from, and a log that cannot be
    attributed to a device is worse than no log at all.
    """
    device = request_device(request)
    if device is None or not device.config.get("log_upload_enabled", False):
        return web.json_response(NO_TOKEN)

    config = request.app["config"]
    body = payloads.to_log_upload_token(device, config.get("bucket_endpoint", ""))
    if not body.get("result"):
        log.warning("Device %d wants to upload its log, but bucket_endpoint %r "
                    "cannot be split into a virtual-host authority",
                    device.petkit_id, config.get("bucket_endpoint", ""))
    return web.json_response(body)


async def handle_upload_log_done(request: web.Request) -> web.Response:
    """`dev_upload_log` — the device reports the log object it just wrote.

    Answers `{"result": "success"}` unconditionally, including for a device we
    do not know and for a report whose file never arrived: this is the last step
    of an upload the device has already completed, and telling it otherwise
    would only make it retry something it cannot redo.

    The report is recorded on the panel's live log rather than in a table. The
    file itself is the record — `web/panel.py` lists the log directory the way
    it lists captures — but `uploadres` and the device's own byte count exist
    only here, and a report with no matching file is the one visible symptom of
    a PUT that never reached us.
    """
    device = request_device(request)
    petkit_id = device.petkit_id if device else to_int(request.query.get("deviceId"), None)
    size = to_int(request.query.get("size"), None)
    key = request.query.get("key", "")
    reason = request.query.get("uploadres", "")

    summary = f"log upload reported: {size if size is not None else '?'} bytes"
    if reason:
        summary += f" ({reason})"
    log.info("Device %s reported a log upload: size=%s reason=%s key=%s",
             petkit_id, size, reason or "-", key)

    # `event_hub`, not `hub`: the device-facing app wires it under that name
    # (main.py) and every other handler here agrees. This asked for `hub`, which
    # only exists on the PANEL app, so it was always None in production and this
    # line never appeared in the live log — while the test passed, because the
    # test set the key production does not use.
    hub = request.app.get("event_hub")
    if hub is not None:
        hub.publish("devlog", petkit_id, summary,
                    detail={"size": size, "key": key, "uploadres": reason})

    return web.json_response(UPLOAD_LOG_DONE)
