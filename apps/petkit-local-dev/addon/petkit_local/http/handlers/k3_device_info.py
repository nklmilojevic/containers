"""dev_k3_device_info — everything a litter box knows about its Pura Air spray.

The K3 is the one accessory `dev_ble_device` never lists (see
`handlers/ble_device.py`). Its binding travels inside the parent's
`dev_device_info` as `k3Device`, and its own detail arrives here: the endpoint
the T4 calls when its heartbeat decides `heart beat need k3_dev_cfg get`.

Unhandled until now, so it fell through to `handle_catchall` and answered
`{"result": {}}` — a device asking what its spray is configured as, told
nothing, forever (issue #17).

What the reply may contain is not a guess. T4 firmware 1.652's
`_pk_WAN_parse_item_k3_info` logs its way through the payload key by key
(`##########ENTRY Prase K3 info#########`), and the fields below are that list
in that order: `battery`, `firmware`, `hardware`, `lighting`, `liquid`,
`refreshing`, `settings{liquidLackSwitch, fixedTimeRefresh}`, `voltage`,
`relateT4`, then `id`/`secret`/`mac`/`sn`.

Two things that list settles:

* Every key is looked up individually, so an absent one is skipped rather than
  read as zero. That is what makes it safe to send only what we actually know
  and omit the rest — which is what this does. A K3 that has never reported its
  battery gets no `battery` key, not a fabricated one.
* The parser only writes flash on a change (`same ID,not save.`,
  `same srt,not save.`, `diff ID !!!%s_%d`), so re-serving the same identity on
  every poll costs the device nothing.

`relateT4` is deliberately NOT sent. Its name and its `%d` admit two readings —
the parent's id, or a 0/1 "is bound to a T4" flag — and nothing available here
separates them. A wrong id lands on the `diff ID` path; the field is left out
until somebody can read the branch that consumes it.
"""
from __future__ import annotations

from typing import Any

from aiohttp import web

from petkit_local.devices.ble import K3_SETTING_KEYS
from petkit_local.http.handlers._common import no_device_response, request_device

#: Which of the K3's reported values map to which key of this reply.
#:
#: `battery` and `liquid` are the two the parent piggybacks on its own
#: `property/post` (`mqtt/bridge.py::_update_linked_k3`), so they are the two
#: that are ever populated today. The rest are named because the firmware reads
#: them and a future frame may carry them, not because we have seen one.
_STATE_FIELDS = {
    "battery": ("consumables", "battery"),
    "liquid": ("consumables", "liquid"),
    "voltage": ("states", "voltage"),
    "lighting": ("states", "lighting"),
    "refreshing": ("states", "refreshing"),
    "firmware": ("states", "firmware"),
    "hardware": ("states", "hardware"),
}


async def handle_k3_device_info(request: web.Request) -> web.Response:
    """Describe the K3 paired to the requesting litter box.

    Returns:
        ``{"result": {...}}`` carrying the spray's identity, whatever of its
        state has been reported, and its settings block if one is known. The
        standard empty result when the device is unknown or has no K3 — which
        is also what the firmware sees for a box that never had one, and is
        what it received for every box until this endpoint existed.
    """
    device = request_device(request)
    if not device:
        return no_device_response()

    ble_registry = request.app.get("ble_registry")
    k3 = ble_registry.get_linked_k3(device.petkit_id) if ble_registry else None
    if k3 is None:
        return no_device_response()

    result: dict[str, Any] = {
        "id": k3.petkit_id,
        "secret": k3.secret,
        "mac": k3.wire_mac,
    }
    if k3.serial_number:
        result["sn"] = k3.serial_number

    for key, (section, field) in _STATE_FIELDS.items():
        value = (k3.state.get(section) or {}).get(field)
        if value is not None:
            result[key] = value

    settings = {k: k3.settings[k] for k in K3_SETTING_KEYS if k in k3.settings}
    if settings:
        result["settings"] = settings

    return web.json_response({"result": result})
