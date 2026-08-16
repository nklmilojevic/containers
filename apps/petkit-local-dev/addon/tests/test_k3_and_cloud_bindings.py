"""The K3's own endpoint, and reading a pairing back out of PetKit's replies.

Two halves of one problem. A Pura Air spray is the accessory `dev_ble_device`
never lists, so everything about it travels inside the parent's traffic: the
binding in `dev_device_info`, the detail in a `dev_k3_device_info` we answered
with `{"result": {}}` until now (issue #17). And the `secret` that binding needs
exists nowhere but the account — which proxy mode carries straight past us and
used to throw away (issue #6).

The field names here are not guesses. T4 firmware 1.652 parses the K3 payload
key by key with a log line for each (`##########ENTRY Prase K3 info#########`),
and `t4/dev_k3_device_info` is a literal string in the same image.
"""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from petkit_local.devices.ble import (
    CLOUD_BLE_TYPES, K3_DEFAULT_CONFIG, BLERegistry, cloud_bindings,
)
from petkit_local.devices.registry import DeviceRegistry
from petkit_local.http.server import create_app
from petkit_local.web.hub import EventHub
from petkit_local.web.panel import create_panel_app

HDR = {"X-Device": "id=10&sn=SN10"}
DEVICE_CONFIG = {"api_url": "http://x/6/", "mqtt_port": 1883, "proxy_mode": False,
                 "proxy_upstream": "", "proxy_block_run_cmd": True, "capture": False}


async def _client(app):
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


def _registries():
    reg = DeviceRegistry()
    reg.get_or_create(petkit_id=10, device_type="t4", serial_number="SN10")
    return reg, BLERegistry()


async def _device_app(reg, ble):
    app = create_app(reg, dict(DEVICE_CONFIG))
    app["ble_registry"] = ble
    return await _client(app)


# --- dev_k3_device_info -----------------------------------------------------

async def test_a_box_with_no_k3_gets_the_same_empty_result_it_always_did():
    """The endpoint is new; the answer for a box that never had a spray is not.
    Every T4 in the field has been receiving `{"result": {}}` here through the
    catch-all, so this path must not start saying something different."""
    reg, ble = _registries()
    c = await _device_app(reg, ble)
    try:
        r = await c.get("/6/t4/dev_k3_device_info", headers=HDR)
        assert r.status == 200
        assert await r.json() == {"result": {}}
    finally:
        await c.close()


async def test_an_unidentified_caller_is_told_nothing_about_anybody_s_spray():
    reg, ble = _registries()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF",
                 secret="s3cret", link_with=10)
    c = await _device_app(reg, ble)
    try:
        r = await c.get("/6/t4/dev_k3_device_info")
        assert await r.json() == {"result": {}}
    finally:
        await c.close()


async def test_the_spray_s_identity_is_served_in_the_shape_the_parser_reads():
    """`secret:%s`, `different mac, dev[%s]server[%s]`, `sn:%s` — and the MAC
    goes out lowercase because that is how every captured cloud reply spells
    one, which is the only thing the comparison could be against."""
    reg, ble = _registries()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF",
                 secret="s3cret", serial_number="K3SN", link_with=10)
    c = await _device_app(reg, ble)
    try:
        result = (await (await c.get("/6/t4/dev_k3_device_info", headers=HDR)).json())["result"]
        assert result["id"] == 700
        assert result["secret"] == "s3cret"
        assert result["mac"] == "aabbccddeeff"
        assert result["sn"] == "K3SN"
    finally:
        await c.close()


async def test_a_reading_the_spray_has_never_sent_is_absent_not_zero():
    """Every key in that parser is looked up individually, so an absent one is
    skipped. A fabricated `battery: 0` would be indistinguishable from a flat
    battery — and we would have invented it."""
    reg, ble = _registries()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF", link_with=10)
    ble.get(700).state["consumables"] = {"battery": 88}
    c = await _device_app(reg, ble)
    try:
        result = (await (await c.get("/6/t4/dev_k3_device_info", headers=HDR)).json())["result"]
        assert result["battery"] == 88
        assert "liquid" not in result
        assert "voltage" not in result
    finally:
        await c.close()


async def test_the_settings_block_appears_only_once_a_real_value_exists():
    """No default for these two: nothing has ever shown us what PetKit sends,
    and a guessed `fixedTimeRefresh` is a spray schedule nobody asked for."""
    reg, ble = _registries()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF", link_with=10)
    c = await _device_app(reg, ble)
    try:
        first = (await (await c.get("/6/t4/dev_k3_device_info", headers=HDR)).json())["result"]
        assert "settings" not in first

        ble.get(700).settings = {"liquidLackSwitch": 1, "fixedTimeRefresh": 0}
        second = (await (await c.get("/6/t4/dev_k3_device_info", headers=HDR)).json())["result"]
        assert second["settings"] == {"liquidLackSwitch": 1, "fixedTimeRefresh": 0}
    finally:
        await c.close()


def test_a_new_k3_starts_with_the_parameters_the_parent_actually_parses():
    """We used to serve `k3Config: {"config": {}}` — six keys the firmware reads
    by name, and an empty object to read them out of."""
    ble = BLERegistry()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF", link_with=10)
    assert ble.get(700).config == K3_DEFAULT_CONFIG
    assert set(K3_DEFAULT_CONFIG) == {
        "standard", "lightness", "lowVoltage",
        "refreshTotalTime", "singleRefreshTime", "singleLightTime",
    }


def test_a_k3_that_already_has_parameters_keeps_them():
    ble = BLERegistry()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF", link_with=10,
                 config={"lightness": 40})
    assert ble.get(700).config == {"lightness": 40}


# --- reading a binding out of a cloud reply ---------------------------------

def test_the_relay_list_yields_one_binding_per_entry():
    payload = {"result": {"list": [
        {"id": 400000001, "mac": "aabbccddeeff", "secret": "s1", "interval": 240, "type": 14},
        {"id": 400000003, "mac": "112233445566", "secret": "s3", "interval": 240, "type": 24},
    ], "nextTick": 3600}}
    found = cloud_bindings("dev_ble_device", payload, 10)
    assert [b["ble_type"] for b in found] == ["w5", "ctw3"]
    assert [b["petkit_id"] for b in found] == [400000001, 400000003]
    assert all(b["link_with"] == 10 for b in found)
    # Stored canonical so a relayed frame in any spelling still matches it.
    assert found[0]["mac"] == "AABBCCDDEEFF"
    assert found[0]["secret"] == "s1"


def test_an_unrecognised_type_imports_nameless_rather_than_as_its_neighbour():
    """`ble_type` picks the frame parser. 14 is shared by W5 and W4 and 24 by
    CTW3 and CTW2, so inverting the whole table would have to guess which — and
    a CTW2 read at CTW3 offsets produces confident nonsense."""
    payload = {"result": {"list": [
        {"id": 1, "mac": "aabbccddeeff", "secret": "s", "type": 99},
    ]}}
    found = cloud_bindings("dev_ble_device", payload, 10)
    assert found[0]["ble_type"] == ""
    # The number itself survives, so the parent is still told to scan for
    # exactly what the account said.
    assert found[0]["scan_type"] == 99
    assert 99 not in CLOUD_BLE_TYPES


@pytest.mark.parametrize("entry", [
    {"id": 1, "secret": "s", "type": 14},                    # no MAC to match on
    {"mac": "aabbccddeeff", "secret": "s", "type": 14},      # no id
    {"id": 0, "mac": "aabbccddeeff", "type": 14},            # id 0 means unidentified
    {"id": 1, "mac": "nonsense", "type": 14},
    "not an object",
])
def test_an_entry_nothing_could_ever_reach_is_not_offered(entry):
    payload = {"result": {"list": [entry]}}
    assert cloud_bindings("dev_ble_device", payload, 10) == []


def test_the_k3_comes_out_of_device_info_with_its_parameters():
    """The one accessory `dev_ble_device` never lists. Its binding is nested
    beside the parent's own fields, and `k3Config.config` is a real cloud reply
    to a real T4."""
    payload = {"result": {
        "id": 10, "withK3": 1,
        "k3Device": {"id": 700, "mac": "aabbccddeeff", "sn": "K3SN", "secret": "s3cret"},
        "settings": {"k3Config": {"config": {"lightness": 100, "standard": [5, 30]}}},
    }}
    found = cloud_bindings("dev_device_info", payload, 10)
    assert len(found) == 1
    assert found[0]["ble_type"] == "k3"
    assert found[0]["petkit_id"] == 700
    assert found[0]["secret"] == "s3cret"
    assert found[0]["serial_number"] == "K3SN"
    assert found[0]["config"] == {"lightness": 100, "standard": [5, 30]}
    # A K3 is never scanned for, so it has no scan type to carry.
    assert "scan_type" not in found[0]


def test_the_parents_own_identity_is_not_mistaken_for_an_accessory():
    """`dev_device_info` is mostly ABOUT the parent. Without `k3Device` there is
    no accessory in it, however many ids the body carries."""
    payload = {"result": {"id": 10, "sn": "SN10", "secret": "parent", "withK3": 0}}
    assert cloud_bindings("dev_device_info", payload, 10) == []


def test_the_spray_s_own_endpoint_yields_its_settings():
    payload = {"result": {
        "id": 700, "mac": "aabbccddeeff", "secret": "s3cret", "battery": 90,
        "settings": {"liquidLackSwitch": 1, "fixedTimeRefresh": 0},
    }}
    found = cloud_bindings("dev_k3_device_info", payload, 10)
    assert found[0]["settings"] == {"liquidLackSwitch": 1, "fixedTimeRefresh": 0}
    # Kept apart from `config`: merging them would put `liquidLackSwitch`
    # inside a `k3Config` the firmware parses for six other keys.
    assert "config" not in found[0]


@pytest.mark.parametrize("payload", [
    None, [], "", {}, {"result": None}, {"result": []}, {"result": {"list": "nope"}},
])
def test_a_reply_that_is_not_the_shape_we_expected_yields_nothing(payload):
    assert cloud_bindings("dev_ble_device", payload, 10) == []




# --- applying what the account said -----------------------------------------

def test_an_accessory_we_do_not_have_is_paired():
    ble = BLERegistry()
    dev, outcome = ble.apply_cloud_binding(
        {"ble_type": "w5", "petkit_id": 700, "mac": "AABBCCDDEEFF",
         "secret": "s", "link_with": 10})
    assert outcome == "imported"
    assert ble.get(700).secret == "s"
    assert dev.link_with == 10


def test_a_secret_the_account_disagrees_with_wins():
    """The valuable case, and the reason to ask at all. A wrong hand-typed
    secret gives a fountain that pairs, relays nothing, and looks healthy."""
    ble = BLERegistry()
    ble.register(ble_type="w5", petkit_id=700, mac="AABBCCDDEEFF",
                 secret="typo", link_with=10)
    _, outcome = ble.apply_cloud_binding(
        {"ble_type": "w5", "petkit_id": 700, "mac": "AABBCCDDEEFF",
         "secret": "real", "link_with": 10})
    assert outcome == "updated"
    assert ble.get(700).secret == "real"


def test_importing_the_same_answer_twice_changes_nothing():
    """The button is pressable repeatedly, so the second press has to be a
    no-op rather than a second round of HA republishing."""
    ble = BLERegistry()
    fields = {"ble_type": "w5", "petkit_id": 700, "mac": "AABBCCDDEEFF",
              "secret": "s", "link_with": 10}
    assert ble.apply_cloud_binding(fields)[1] == "imported"
    assert ble.apply_cloud_binding(dict(fields))[1] == "unchanged"


def test_a_type_this_build_cannot_parse_is_refused_by_name():
    """`ble_type` selects the frame decoder, so an unknown number must not be
    guessed into one — and the refusal has to say so, not vanish."""
    ble = BLERegistry()
    dev, outcome = ble.apply_cloud_binding(
        {"ble_type": "", "petkit_id": 700, "scan_type": 99,
         "mac": "AABBCCDDEEFF", "link_with": 10})
    assert dev is None
    assert "99" in outcome
    assert ble.all() == []


def test_a_mac_already_paired_under_another_id_is_refused():
    ble = BLERegistry()
    ble.register(ble_type="w5", petkit_id=701, mac="AABBCCDDEEFF", link_with=10)
    dev, outcome = ble.apply_cloud_binding(
        {"ble_type": "w5", "petkit_id": 700, "mac": "AABBCCDDEEFF", "link_with": 10})
    assert dev is None
    assert "701" in outcome


def test_a_k3s_parameters_are_replaced_wholesale_not_merged():
    """`register` only overwrites truthy values, which is right for a parent
    re-reporting an accessory and wrong for parameters the account owns."""
    ble = BLERegistry()
    ble.register(ble_type="k3", petkit_id=700, mac="AABBCCDDEEFF", link_with=10,
                 config={"lightness": 40, "stale": 1})
    ble.apply_cloud_binding({"ble_type": "k3", "petkit_id": 700,
                             "mac": "AABBCCDDEEFF", "link_with": 10,
                             "config": {"lightness": 100}})
    assert ble.get(700).config == {"lightness": 100}


# --- the one-shot panel action ----------------------------------------------

def _panel(reg, ble, upstream=""):
    cfg = {"api_url": "http://x/6/", "capture": False, "capture_dir": "/nope"}
    return create_panel_app(reg, ble, EventHub(), cfg, None,
                            live_config={"proxy_upstream": upstream})


async def _fake_cloud(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    c = TestClient(TestServer(app))
    await c.start_server()
    return c, str(c.make_url("")).rstrip("/")


async def test_the_button_asks_petkit_and_pairs_what_it_answers():
    asked = []

    async def cloud(request):
        asked.append(request.path)
        if request.path.endswith("dev_ble_device"):
            return web.json_response({"result": {"list": [
                {"id": 400000001, "mac": "aabbccddeeff", "secret": "real",
                 "interval": 240, "type": 14}]}})
        return web.json_response({"result": {}})

    reg, ble = _registries()
    up, base = await _fake_cloud(cloud)
    c = await _client(_panel(reg, ble, base))
    try:
        body = await (await c.post("/api/ble/import",
                                   data=json.dumps({"device_id": 10}))).json()
        assert body["imported"] == 1
        assert ble.get(400000001).secret == "real"
        # All three are asked: a K3 is in none of the places a fountain is.
        assert sorted(p.rsplit("/", 1)[-1] for p in asked) == [
            "dev_ble_device", "dev_device_info", "dev_k3_device_info"]
    finally:
        await c.close()
        await up.close()


async def test_a_refusal_on_one_endpoint_does_not_lose_the_others():
    """A device with no K3 is refused `dev_k3_device_info` while its fountains
    list perfectly well."""
    async def cloud(request):
        if request.path.endswith("dev_ble_device"):
            return web.json_response({"result": {"list": [
                {"id": 400000001, "mac": "aabbccddeeff", "secret": "s",
                 "type": 14}]}})
        return web.json_response({"error": {"code": 401, "msg": "no"}})

    reg, ble = _registries()
    up, base = await _fake_cloud(cloud)
    c = await _client(_panel(reg, ble, base))
    try:
        body = await (await c.post("/api/ble/import",
                                   data=json.dumps({"device_id": 10}))).json()
        assert body["imported"] == 1
        # And the refusals are reported rather than swallowed.
        assert any("401" in str(r.get("outcome")) for r in body["results"])
    finally:
        await c.close()
        await up.close()


async def test_an_unreachable_cloud_is_an_error_not_a_silent_nothing():
    reg, ble = _registries()
    c = await _client(_panel(reg, ble, "http://127.0.0.1:1"))
    try:
        r = await c.post("/api/ble/import", data=json.dumps({"device_id": 10}))
        assert r.status == 502
        assert "PetKit" in (await r.json())["error"]
    finally:
        await c.close()


async def test_an_id_a_real_device_already_owns_is_refused():
    """An accessory shares the `petkit_{id}` HA identity with a real device, so
    a collision makes two devices fight over one entity set."""
    async def cloud(request):
        if request.path.endswith("dev_ble_device"):
            return web.json_response({"result": {"list": [
                {"id": 10, "mac": "aabbccddeeff", "secret": "s", "type": 14}]}})
        return web.json_response({"result": {}})

    reg, ble = _registries()
    up, base = await _fake_cloud(cloud)
    c = await _client(_panel(reg, ble, base))
    try:
        body = await (await c.post("/api/ble/import",
                                   data=json.dumps({"device_id": 10}))).json()
        assert body["imported"] == 0
        assert ble.get(10) is None
        assert any("already a real device" in str(r["outcome"]) for r in body["results"])
    finally:
        await c.close()
        await up.close()


@pytest.mark.parametrize("body", ["not json", '["a list"]'])
async def test_a_malformed_request_is_refused(body):
    reg, ble = _registries()
    c = await _client(_panel(reg, ble))
    try:
        assert (await c.post("/api/ble/import", data=body)).status == 400
    finally:
        await c.close()


async def test_importing_for_a_device_we_do_not_have_is_a_404():
    reg, ble = _registries()
    c = await _client(_panel(reg, ble))
    try:
        r = await c.post("/api/ble/import", data=json.dumps({"device_id": 999}))
        assert r.status == 404
    finally:
        await c.close()


async def test_a_device_that_cannot_have_a_spray_is_not_asked_about_one():
    """PetKit answers 404 to a feeder asking `dev_k3_device_info`, and the
    firmware string behind it is `t4/…`. Confirmed against the real cloud on a
    T5: HTTP 404. Skipping it keeps a meaningless refusal out of the report."""
    asked = []

    async def cloud(request):
        asked.append(request.path.rsplit("/", 1)[-1])
        return web.json_response({"result": {}})

    reg = DeviceRegistry()
    reg.get_or_create(petkit_id=11, device_type="d4h", serial_number="SNF")
    ble = BLERegistry()
    up, base = await _fake_cloud(cloud)
    c = await _client(_panel(reg, ble, base))
    try:
        await c.post("/api/ble/import", data=json.dumps({"device_id": 11}))
        assert "dev_k3_device_info" not in asked
        assert "dev_ble_device" in asked
    finally:
        await c.close()
        await up.close()


async def test_an_endpoint_this_model_does_not_have_is_not_reported_as_a_problem():
    """Confirmed against the real cloud: PetKit answers 404 to a T5 asking
    `dev_k3_device_info`. That is the endpoint being a T4's, not a failure."""
    async def cloud(request):
        if request.path.endswith("dev_k3_device_info"):
            return web.Response(status=404)
        return web.json_response({"result": {}})

    reg, ble = _registries()
    up, base = await _fake_cloud(cloud)
    c = await _client(_panel(reg, ble, base))
    try:
        body = await (await c.post("/api/ble/import",
                                   data=json.dumps({"device_id": 10}))).json()
        assert body["results"] == []
    finally:
        await c.close()
        await up.close()
