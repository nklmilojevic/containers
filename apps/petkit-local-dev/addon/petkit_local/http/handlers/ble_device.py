"""dev_ble_device — the BLE accessories a WiFi device proxies for.

A K3 Pura Air spray or W5 fountain has no WiFi of its own; it is reached over BLE by a
mains-powered neighbour (a T4, say), which relays its data to us. This endpoint
is how that WiFi device learns which accessories it is supposed to talk to, so
an empty answer here means the device stops relaying.

K3 sprays are deliberately excluded: they are reported inside the parent's
`dev_device_info` payload instead (see devices/ble.py).
"""
from __future__ import annotations

from aiohttp import web

from petkit_local.http.handlers._common import no_device_response, request_device


async def handle_ble_device(request: web.Request) -> web.Response:
    """List the BLE accessories this device should scan for and relay.

    Returns:
        ``{"result": {"list": [...], "nextTick": 3600}}`` where each entry comes
        from `BLEDevice.to_ble_list_entry()`, or the standard empty result when
        the device cannot be identified at all.

        WITH NOTHING PAIRED THE `list` KEY IS OMITTED ENTIRELY, and only
        `nextTick` is sent. 1.8.1 changed this to send `list: []`, on the
        grounds that PetKit's own cloud does — captured from it answering an
        unaccessorised device 234 times in one session — and that the firmware's
        `ERR:...parse item NULL` was therefore a logged parse error rather than
        a fatal one. That argument was from analogy, and owners reported the
        empty array crashing devices in the field, so 1.8.2 put the omission
        back. We do not have a model or a firmware build for those reports, and
        so cannot say WHICH devices or why the cloud gets away with it; what we
        do have is a shape that ran for months without the complaint. When a
        report arrives with a capture, this is the comment to correct.

        There is a second, independent reason to leave this alone, and it
        cuts the other way: `pk_schmg_parse_ble_dev_list` (T5, D4SH and W7H
        all share `ble_relay_network.c`) rejects the WHOLE body below 40 bytes
        with `ble list len too short`, before it parses anything. Our empty
        answer is 30 bytes over HTTP and 28 compact over MQTT, so on that
        family it is discarded entirely -- `nextTick` included, despite the
        paragraph below. Adding `list` is what pushes it past the gate and into
        the parser, which is where the field reports came from. So the shape
        that works does so by never being read, and anything that lengthens it
        by ~10 bytes would silently re-enter the path that broke. `tests/`
        holds that line.

        `nextTick` stays either way, and is not implicated in any of it. It
        tells the parent when to come back for the list, and omitting it left a
        device with nothing paired holding no schedule at exactly the moment it
        had nothing else to go on.
    """
    device = request_device(request)
    if not device:
        return no_device_response()

    ble_registry = request.app.get("ble_registry")
    ble_list = []

    if ble_registry:
        for ble_dev in ble_registry.non_k3_for_parent(device.petkit_id):
            ble_list.append(ble_dev.to_ble_list_entry())

    result: dict = {"nextTick": 3600}
    if ble_list:
        result["list"] = ble_list
    return web.json_response({"result": result})
