"""Tests for the shared request → device resolution helpers.

Runs the real ``device_middleware`` in front of a probe route, so the X-Device
header goes through the exact parsing path a device's request does. The
hostile-input cases are the point of the module: the handlers this replaces
used a bare ``int()`` and answered a non-numeric device id with an HTTP 500.
"""
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.http.handlers._common import (
    device_id, device_serial, no_device_response, request_device,
)
from petkit_local.http.middleware import device_middleware

PROBE_PATH = "/6/t5/probe"


async def _probe(request: web.Request) -> web.Response:
    device = request_device(request)
    return web.json_response({
        "id": device_id(request),
        "sn": device_serial(request),
        "device": device.petkit_id if device else None,
    })


async def _client(registry: DeviceRegistry | None) -> TestClient:
    app = web.Application(middlewares=[device_middleware])
    if registry is not None:
        app["registry"] = registry
    app.router.add_get(PROBE_PATH, _probe)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _probe_json(registry, headers=None, query="") -> dict:
    client = await _client(registry)
    try:
        r = await client.get(PROBE_PATH + query, headers=headers or {})
        assert r.status == 200
        return await r.json()
    finally:
        await client.close()


def _registry_with(petkit_id: int, serial: str = "") -> DeviceRegistry:
    reg = DeviceRegistry()
    reg.get_or_create(petkit_id=petkit_id, device_type="t5", serial_number=serial)
    return reg


def _mocked(x_device=None, query="", form=None) -> web.Request:
    """A request that bypasses the middleware, for values a real header cannot
    carry (e.g. a 5000-digit id would exceed aiohttp's header size limit)."""
    request = make_mocked_request("GET", PROBE_PATH + query)
    if x_device is not None:
        request["x_device"] = x_device
    if form is not None:
        request["form"] = form
    return request


async def test_numeric_id_from_header():
    data = await _probe_json(_registry_with(100, "SN100"), {"X-Device": "id=100&sn=SN100"})
    assert data["id"] == 100
    assert data["sn"] == "SN100"
    assert data["device"] == 100


async def test_missing_id_is_none_not_zero():
    data = await _probe_json(_registry_with(100))
    assert data["id"] is None
    assert data["sn"] == ""
    assert data["device"] is None


async def test_non_numeric_id_does_not_raise():
    # The bug being fixed: `int(x_dev.get("id", 0))` on "abc" raised
    # ValueError, which aiohttp turned into a 500. A malformed id is simply an
    # unidentified device.
    data = await _probe_json(_registry_with(100), {"X-Device": "id=abc&sn=SN100"})
    assert data["id"] is None


async def test_empty_id_value_does_not_raise():
    # `id=` survives header parsing as the empty string (parse_qs is called
    # with keep_blank_values=True), which is what broke bare int().
    data = await _probe_json(_registry_with(100), {"X-Device": "id=&sn=SN100"})
    assert data["id"] is None


async def test_unknown_id_resolves_to_no_device():
    reg = _registry_with(100, "SN100")
    data = await _probe_json(reg, {"X-Device": "id=999&sn=SN999"})
    assert data["id"] == 999
    assert data["device"] is None
    # resolution must never register the unknown device
    assert [d.petkit_id for d in reg.all()] == [100]


async def test_serial_fallback_when_id_is_unusable():
    data = await _probe_json(_registry_with(100, "SN100"), {"X-Device": "id=junk&sn=SN100"})
    assert data["id"] is None
    assert data["device"] == 100


async def test_serial_fallback_when_id_is_unknown():
    # A known serial rescues a request whose id we have never seen.
    data = await _probe_json(_registry_with(100, "SN100"), {"X-Device": "id=777&sn=SN100"})
    assert data["device"] == 100


async def test_id_wins_over_serial():
    reg = DeviceRegistry()
    reg.get_or_create(petkit_id=100, device_type="t5", serial_number="SN100")
    reg.get_or_create(petkit_id=200, device_type="t5", serial_number="SN200")
    data = await _probe_json(reg, {"X-Device": "id=200&sn=SN100"})
    assert data["device"] == 200


async def test_header_id_wins_over_query_id():
    reg = DeviceRegistry()
    reg.get_or_create(petkit_id=100, device_type="t5")
    reg.get_or_create(petkit_id=200, device_type="t5")
    data = await _probe_json(reg, {"X-Device": "id=100"}, query="?id=200")
    assert data["id"] == 100
    assert data["device"] == 100


async def test_query_id_used_when_header_absent():
    data = await _probe_json(_registry_with(100), query="?id=100")
    assert data["id"] == 100
    assert data["device"] == 100


async def test_query_id_used_when_header_id_is_unusable():
    # The header is tried first but does not veto: an unusable header value
    # falls through to the query parameter.
    data = await _probe_json(_registry_with(100), {"X-Device": "id=abc"}, query="?id=100")
    assert data["id"] == 100


async def test_query_serial_used_when_header_absent():
    data = await _probe_json(_registry_with(100, "SN100"), query="?sn=SN100")
    assert data["sn"] == "SN100"
    assert data["device"] == 100


async def test_missing_registry_returns_none():
    data = await _probe_json(None, {"X-Device": "id=100&sn=SN100"})
    assert data["id"] == 100
    assert data["device"] is None


def test_explicit_registry_overrides_the_app():
    reg = _registry_with(100)
    request = _mocked({"id": "100"})
    assert request_device(request, registry=reg).petkit_id == 100


def test_non_positive_ids_are_treated_as_absent():
    for raw in ("0", "-1", "-0", " 0 "):
        assert device_id(_mocked({"id": raw})) is None


def test_rejects_ids_int_would_silently_accept():
    # int() accepts all of these; none of them is a device id we ever sent.
    for raw in ("1_0", "12.5", "0x64", "+100", "١٢", "1e3", "100abc", "  "):
        assert device_id(_mocked({"id": raw})) is None


def test_oversized_id_does_not_raise():
    # Python 3.11+ raises ValueError converting integer strings above 4300
    # digits, so the guard has to survive a padded id too.
    assert device_id(_mocked({"id": "1" * 5000})) is None


def test_surrounding_whitespace_is_tolerated():
    assert device_id(_mocked({"id": " 100 "})) == 100
    assert device_serial(_mocked({"sn": " SN100 "})) == "SN100"


def test_non_string_id_values_do_not_raise():
    for raw in (None, [], {}, 3.5, True, object()):
        assert device_id(_mocked({"id": raw})) is None
    assert device_id(_mocked({"id": 100})) == 100


def test_blank_serial_falls_through_to_query():
    assert device_serial(_mocked({"sn": "   "}, query="?sn=SN100")) == "SN100"
    assert device_serial(_mocked({"sn": ""})) == ""


def test_blank_serial_does_not_match_a_serial_less_device():
    # by_serial("") must never be reached, or every request without a serial
    # would resolve to the first device registered without one.
    reg = _registry_with(100, serial="")
    assert request_device(_mocked({"id": "junk"}), registry=reg) is None


def test_no_device_response_shape():
    resp = no_device_response()
    assert resp.status == 200
    assert resp.body == b'{"result": {}}'


# --- the third source: an urlencoded POST body ------------------------------
#
# Some models send no `X-Device` and no query string at all — a Feeder D4 puts
# its whole identity in the body. `http/middleware/` parses it once into
# `request["form"]` so these accessors can stay synchronous.

def test_identity_can_come_from_the_body_alone():
    req = _mocked(form={"id": "400090690", "sn": "20241223G11497"})
    assert device_id(req) == 400090690
    assert device_serial(req) == "20241223G11497"


def test_the_body_is_the_last_resort_not_the_first():
    """Header beats query beats body. Nobody has seen a device send more than
    one, but a precedence that is not decided is a precedence that varies."""
    req = _mocked({"id": "1", "sn": "H"}, query="?id=2&sn=Q",
                  form={"id": "3", "sn": "B"})
    assert device_id(req) == 1
    assert device_serial(req) == "H"

    req = _mocked(query="?id=2&sn=Q", form={"id": "3", "sn": "B"})
    assert device_id(req) == 2
    assert device_serial(req) == "Q"


def test_an_unusable_value_falls_through_to_the_body():
    """The fallback fires on unusable, not merely absent — the same rule the
    header/query pair already follows."""
    assert device_id(_mocked({"id": "abc"}, form={"id": "400090690"})) == 400090690
    assert device_serial(_mocked({"sn": "  "}, form={"sn": "SN"})) == "SN"


def test_a_junk_body_identifies_nothing():
    assert device_id(_mocked(form={"id": "abc"})) is None
    assert device_id(_mocked(form={})) is None
    assert device_serial(_mocked(form={})) == ""


def test_device_field_reads_any_name_the_same_way():
    """`handlers/signup.py` uses it for `mac`, `firmware` and `bt_mac`, which
    are not identity but arrive by the same three routes."""
    from petkit_local.http.handlers._common import device_field

    req = _mocked(query="?mac=QUERY", form={"mac": "BODY", "firmware": "1.267"})
    assert device_field(req, "mac") == "QUERY"
    assert device_field(req, "firmware") == "1.267"
    assert device_field(req, "absent") is None
