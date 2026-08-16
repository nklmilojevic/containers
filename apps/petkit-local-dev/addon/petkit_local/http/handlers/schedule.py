"""dev_schedule_get — the litter box's scheduled cleaning times.

Like the feeder's schedule, this is executed by the device against its own
clock, not driven from here, so the response has to be a valid schedule under
all circumstances — including for a device we cannot identify. A valid schedule
with nothing in it is an empty array, and that is what an unset one gets.

**There is no default cleaning schedule**, and there must not be: a schedule
served from here is one the owner never chose and cannot see, and the box runs
it. Not even a plausible one — a T5 was watched being sent 09:45/13:45/18:45 by
PetKit's own cloud, which makes those times some real account's schedule, not
this account's.

Same rule as `devices/defaults.py::default_settings`: a value we make up does not
stay ours, because it is served straight back to the device as the owner's
setting.
"""
from __future__ import annotations

from aiohttp import web

from petkit_local.http.handlers._common import request_device


async def handle_schedule_get(request: web.Request) -> web.Response:
    """Return the device's stored cleaning schedule, or nothing at all.

    Returns:
        ``{"result": [...]}`` — verbatim ``device.config["schedule"]`` when one
        has been set, otherwise ``{"result": []}``: a well-formed schedule that
        schedules nothing, exactly as `dev_feed_get` answers a feeder with no
        meals rather than inventing one.

    One array carries BOTH of the box's timed jobs — `type: 0` is a cleaning and
    `type: 1` a periodic deodorizing (`events/codes.py::SCHEDULE_TYPES`) — so
    anything that rewrites a stored schedule has to keep the entries it does not
    own. `web/api/schedules.py::api_save_schedule` is where that is enforced.

    A stored schedule is served back verbatim, which matters because the shape
    differs by transport: the real cloud's HTTP reply also carries `deviceId` and
    an ISO-8601 `updatedAt` per entry, while the `property.set` write that sets
    one carries only `id`/`repeats`/`time`/`type`. Whichever the panel stored is
    what the device gets.
    """
    device = request_device(request)
    schedule = device.config.get("schedule") if device else None
    return web.json_response({"result": schedule or []})
