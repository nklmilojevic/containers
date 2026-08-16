"""dev_serverinfo — tells the device which server to talk to (us).

This is the endpoint that makes the takeover stick. A device asks it, at boot
and periodically, where the API lives; the official cloud answers with its own
address, and we answer with ours. Getting `api_url` wrong here — a host the
device cannot resolve, a scheme it will not use — hands the device back to
PetKit at the next poll, or strands it with no server at all.
"""
from __future__ import annotations

from aiohttp import web

from petkit_local.devices import payloads
from petkit_local.devices.base import Device
from petkit_local.http.handlers._common import request_device


async def handle_serverinfo(request: web.Request) -> web.Response:
    """Point the device at this server.

    Returns:
        `payloads.to_serverinfo()`'s body, which documents where each field value
        comes from. An unregistered device gets the same answer built from a
        throwaway `Device`, so it is still told where to go — and so the shape
        cannot drift between the two paths, which it did while this handler
        kept its own copy of the literal.
    """
    device = request_device(request)

    api_url = request.app["config"]["api_url"]

    if device:
        return web.json_response(payloads.to_serverinfo(device, api_url))

    return web.json_response(
        payloads.to_serverinfo(
            Device(device_type=request.get("device_type", "unknown"), petkit_id=0),
            api_url))
