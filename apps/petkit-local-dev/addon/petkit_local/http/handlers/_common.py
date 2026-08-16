"""Shared request → device resolution for the device-facing HTTP handlers.

Every handler needs the same thing first: "which registered device is talking
to me?". That snippet was copy-pasted into roughly ten handlers plus the
logging middleware, each with undocumented variations — some coerced the id
with a bare ``int()`` (which raises ValueError on a device-supplied
non-numeric id, turning a malformed request into an HTTP 500), some fell back
to a serial-number lookup and some did not, and some read the id from the
X-Device header while others read it from the query string. This module is the
single reconciled implementation; the variations it settles are documented on
the functions below.

Deliberate decisions
--------------------
* **Never raises on device input.** Everything a device sends is untrusted
  text. Both accessors return ``None``/``""`` for anything unusable instead of
  propagating an exception into the handler.
* **Header, then query, then body.** The X-Device header is sent by the
  firmware on every request, whereas ``?id=``/``?sn=`` only appear on a few
  endpoints, so the header is tried first and the query string is the
  fallback. Each fallback triggers whenever the previous source does not yield
  a *usable* value, not merely when the key is absent. (The two disagree only
  if a device sends both with different values, which has never been observed.)

  The body is last, and it is there for a model that uses nothing else: a
  Feeder D4 signs up with no header and no query string, everything
  urlencoded in the POST body. Without that fallback it is unidentifiable to
  every endpoint here — signup answers 400 and, worse, ``dev_iot_device_info``
  answers **200** with no MQTT credentials, so the device silently never
  reaches the broker.
* **id before serial.** ``DeviceRegistry.get()`` is an O(1) primary-key lookup
  and the petkit id is the registry's only identity; ``by_serial()`` is an O(n)
  scan that exists purely as a rescue path for requests whose id is missing or
  unusable. So: id first, serial only if that failed.
* **Resolution never creates a device.** ``request_device()`` reports what is
  already registered. Minting devices stays with the two handlers that own
  registration (``signup``, ``iot_device_info``), so a stray request cannot
  populate the registry.
* **Non-positive ids are treated as absent.** ``DeviceRegistry`` drops
  persisted devices with ``petkit_id <= 0`` as phantoms (see
  ``devices/registry.py::_load``), so ``id=0`` means "unidentified", not
  "device zero".
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from petkit_local.utils.coerce import to_int

if TYPE_CHECKING:
    # Type-only: this is a leaf helper imported by every handler, and the
    # names below are needed for annotations, not at runtime.
    from petkit_local.devices.base import Device
    from petkit_local.devices.registry import DeviceRegistry


def _coerce_device_id(value: object) -> int | None:
    """Coerce an untrusted device id into a positive int, or None.

    A shape gate in front of ``utils.coerce.to_int``, which does the parsing.
    The gate is deliberately stricter than ``to_int``: an id is an unsigned
    ASCII decimal integer or it is not an id at all. ``to_int`` on its own
    would read "+100", "12.5" and "1e3" as 100, 12 and 1000, and each of those
    resolves to a *different registered device* instead of to "unidentified" —
    the wrong direction for the field that selects whose credentials and state
    a request touches. Bools are excluded for the same reason (``to_int(True)``
    is 1, a perfectly plausible device id).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        # isascii() rejects non-Latin digit forms such as "١٢"; isdigit()
        # rejects signs, separators, decimal points and exponents. int() and
        # to_int() between them accept all of those.
        if not value.isascii() or not value.isdigit():
            return None
    elif not isinstance(value, int):
        return None
    parsed = to_int(value, None)
    return parsed if parsed is not None and parsed > 0 else None


def device_field(request: web.Request, name: str) -> str | None:
    """One identity field, from whichever of the three places carries it.

    Header, then query string, then the urlencoded POST body
    (``request["form"]``, filled by ``http/middleware/``). The first two are
    the Ingenic models; the third is the only one an ESP32 feeder uses, which
    is why it exists — see `parse_form_body`.

    Returns the first non-blank value, or None.
    """
    x_device = request.get("x_device") or {}
    form = request.get("form") or {}
    for source in (x_device.get(name), request.query.get(name), form.get(name)):
        if isinstance(source, str) and source.strip():
            return source.strip()
    return None


def device_id(request: web.Request) -> int | None:
    """Return the requesting device's petkit id, or None if unidentifiable.

    Reads the X-Device header first (parsed into ``request["x_device"]`` by
    ``http/middleware/``), then the ``id`` query parameter, then the ``id``
    field of an urlencoded POST body.

    Returns:
        A positive int, or None when the id is missing, non-numeric, or <= 0.
        Never raises, whatever the device sent.
    """
    x_device = request.get("x_device") or {}
    form = request.get("form") or {}
    return (_coerce_device_id(x_device.get("id"))
            or _coerce_device_id(request.query.get("id"))
            or _coerce_device_id(form.get("id")))


def device_serial(request: web.Request) -> str:
    """Return the requesting device's serial number, or "" if not supplied.

    Same header-then-query-then-body precedence as :func:`device_id`. The value
    is device-supplied text and is only ever used for an equality lookup, so it
    is passed through unvalidated apart from stripping surrounding whitespace.
    """
    return device_field(request, "sn") or ""


def request_device(request: web.Request, registry: DeviceRegistry | None = None) -> Device | None:
    """Resolve the registered device behind this request, or None.

    Args:
        registry: Registry to look in. Defaults to the one the app was built
            with (``request.app["registry"]``); if the app has none — as in
            narrow unit tests — the result is None rather than a KeyError.

    Returns:
        The registered Device, or None if the request identifies no known
        device. Never creates a device: registration belongs to the signup and
        iot_device_info handlers.
    """
    if registry is None:
        registry = request.app.get("registry")
    if registry is None:
        return None

    petkit_id = device_id(request)
    device = registry.get(petkit_id) if petkit_id is not None else None
    if device is not None:
        return device

    serial = device_serial(request)
    return registry.by_serial(serial) if serial else None


def no_device_response() -> web.Response:
    """Return the standard "I could not identify your device" reply.

    Deliberately a 200 with an empty result object, because that is the shape
    every handler already returns on this path (``{"result": {}}`` appears in
    iot_device_info, ble_device and stubs) and changing the status code would
    change what devices in the field receive. No 4xx helper is provided: only
    ``signup`` answers with an error status, so there is nothing to share.
    """
    return web.json_response({"result": {}})
