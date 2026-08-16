"""Signing a request the way the firmware signs its own.

Everything else in `http/` answers a device. This is the one thing that talks
to PetKit on our own initiative, and it only works if the `X-Device` header we
build is byte-for-byte the header the firmware would have built — PetKit
verifies the MD5 in it and answers `704` to anything else.

The construction is not inferred. T4 firmware 1.652 carries both format strings
in the function that assembles the header::

    id%snonce%stimestamp%utype%s%s
    id=%s&nonce=%s&timestamp=%u&type=%s&sign=%s

so the tests below are that pair written out, plus the two fields that must
come from the device rather than from us.
"""
import hashlib
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from petkit_local.devices.base import Device
from petkit_local.http.cloud_fetch import (
    CloudRefused, fetch_as_device, signature, x_device_header,
)


def _device(**over):
    d = Device(device_type="t5", petkit_id=100, serial_number="SN100")
    d.api_secret = "0123456789abcdef"
    d.wire_type = "T5"
    for k, v in over.items():
        setattr(d, k, v)
    return d


def test_the_signature_is_the_firmware_s_own_format_string():
    """`id%snonce%stimestamp%utype%s%s` — the four fields with their own names,
    no separators, secret appended."""
    expected = hashlib.md5(
        b"id100noncedeadbeeftimestamp1700000000typeT50123456789abcdef").hexdigest()
    assert signature(100, "deadbeef", 1700000000, "T5", "0123456789abcdef") == expected


def test_the_header_is_the_other_format_string():
    header = x_device_header(_device(), nonce="deadbeef", timestamp=1700000000)
    assert header == (
        "id=100&nonce=deadbeef&timestamp=1700000000&type=T5"
        f"&sign={signature(100, 'deadbeef', 1700000000, 'T5', '0123456789abcdef')}")


def test_the_type_is_the_device_s_own_spelling_not_our_codename():
    """`type` is hashed, so `T5` and `t5` are different requests and only one
    authenticates. The device tells us which it uses; we never decide."""
    upper = x_device_header(_device(wire_type="T5"), nonce="n", timestamp=1)
    lower = x_device_header(_device(wire_type="t5"), nonce="n", timestamp=1)
    assert "&type=T5&" in upper and "&type=t5&" in lower
    assert upper.rsplit("sign=", 1)[1] != lower.rsplit("sign=", 1)[1]


def test_a_device_that_has_never_spoken_falls_back_to_its_codename():
    """`wire_type` is recorded from live traffic, so it is empty until the
    device has been seen. Signing with the codename is a guess that may fail —
    but refusing to try at all would fail too, and less usefully."""
    assert "&type=t5&" in x_device_header(_device(wire_type=""), nonce="n", timestamp=1)


def test_two_requests_do_not_reuse_a_nonce():
    d = _device()
    assert x_device_header(d) != x_device_header(d)


# --- what comes back --------------------------------------------------------

async def _cloud(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c, str(c.make_url("")).rstrip("/")


async def test_the_request_goes_where_the_firmware_would_send_it():
    seen = {}

    async def cloud(request):
        seen["path"] = request.path
        seen["xdev"] = request.headers.get("X-Device", "")
        return web.json_response({"result": {"ok": 1}})

    up, base = await _cloud(cloud)
    async with __import__("aiohttp").ClientSession() as s:
        try:
            body = await fetch_as_device(s, base, _device(), "dev_ble_device")
            assert body["result"]["ok"] == 1
            assert seen["path"] == "/6/t5/dev_ble_device"
            assert "sign=" in seen["xdev"]
        finally:
            await up.close()


async def test_a_704_says_the_credential_is_ours_not_petkit_s():
    """The one refusal a user can act on, so it must not read like a generic
    failure: it means we never learned this device's real signing secret."""
    async def cloud(request):
        return web.json_response({"error": {"code": 704, "msg": "sign error"}})

    up, base = await _cloud(cloud)
    async with __import__("aiohttp").ClientSession() as s:
        try:
            with pytest.raises(CloudRefused) as e:
                await fetch_as_device(s, base, _device(), "dev_ble_device")
            assert "704" in str(e.value)
            assert "sign it up" in str(e.value).lower()
        finally:
            await up.close()


@pytest.mark.parametrize("reply, status", [
    ({"error": {"code": 401, "msg": "nope"}}, 200),
    ({"result": {}}, 500),
])
async def test_any_other_refusal_is_still_a_refusal(reply, status):
    async def cloud(request):
        return web.json_response(reply, status=status)

    up, base = await _cloud(cloud)
    async with __import__("aiohttp").ClientSession() as s:
        try:
            with pytest.raises(CloudRefused):
                await fetch_as_device(s, base, _device(), "dev_ble_device")
        finally:
            await up.close()


async def test_a_body_that_is_not_json_is_refused_rather_than_parsed():
    async def cloud(request):
        return web.Response(body=b"<html>maintenance</html>", content_type="text/html")

    up, base = await _cloud(cloud)
    async with __import__("aiohttp").ClientSession() as s:
        try:
            with pytest.raises(CloudRefused):
                await fetch_as_device(s, base, _device(), "dev_ble_device")
        finally:
            await up.close()


def test_the_recorded_wire_type_round_trips_through_the_registry():
    """It is signing input, so it has to survive a restart like any credential."""
    d = _device()
    restored = Device.from_dict(json.loads(json.dumps(d.to_dict())))
    assert restored.wire_type == "T5"
