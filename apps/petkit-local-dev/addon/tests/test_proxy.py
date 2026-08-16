import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from petkit_local.devices.base import Device
from petkit_local.http.proxy import (
    BREAKER_THRESHOLD,
    DEFAULT_UPSTREAM,
    UPSTREAM_PRESETS,
    _record_outcome,
    breaker_is_open,
    close_proxy_session,
    forward,
    get_proxy_session,
    merge_json_result_lists,
    normalize_upstream,
    proxy_request,
    resolve_upstream,
)
from petkit_local.http.redact import RedactionPolicy


async def _upstream(handler):
    """Start a fake PetKit cloud and return (client, base_url)."""
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, str(client.make_url("")).rstrip("/")


async def _proxying_client(base_url, sessions, block_run_cmd=True):
    """Start a client whose every request is forwarded to `base_url`.

    `sessions` collects the session object each proxied call used, so a test can
    assert the pool is shared instead of rebuilt per request.
    """
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        sessions.append(get_proxy_session(request.app))
        return await proxy_request(request, upstream=base_url, block_run_cmd=block_run_cmd)

    app.router.add_route("*", "/{path:.*}", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_proxy_reuses_one_session_across_requests():
    async def echo(request):
        return web.json_response({"result": {"path": request.path}})

    up, base = await _upstream(echo)
    sessions = []
    client = await _proxying_client(base, sessions)
    try:
        r1 = await client.post("/6/t5/dev_ota_check", data=b"a")
        r2 = await client.post("/6/t5/dev_ota_check", data=b"b")
        assert r1.status == 200 and r2.status == 200
        assert (await r1.json())["result"]["path"] == "/6/t5/dev_ota_check"

        assert len(sessions) == 2
        assert sessions[0] is sessions[1]
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_close_proxy_session_closes_and_allows_reopen():
    app = web.Application()
    first = get_proxy_session(app)
    assert get_proxy_session(app) is first

    await close_proxy_session(app)
    assert first.closed

    second = get_proxy_session(app)
    assert second is not first
    await close_proxy_session(app)
    assert second.closed

    # Closing an app that never proxied anything must not raise.
    await close_proxy_session(web.Application())


async def test_proxy_still_strips_run_cmd_from_heartbeat():
    payload = {
        "result": [
            {"time": 1, "content": json.dumps({"user_cmd": {"run_cmd": "rm -rf /"}})},
            {"time": 2, "content": json.dumps({"user_cmd": {"set_state": 1}})},
        ]
    }

    async def heartbeat(request):
        return web.json_response(payload)

    up, base = await _upstream(heartbeat)
    sessions = []
    client = await _proxying_client(base, sessions)
    try:
        r = await client.get("/6/poll/t5/heartbeat")
        body = await r.json()
        assert [e["time"] for e in body["result"]] == [2]
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_proxy_keeps_run_cmd_when_blocking_disabled():
    async def heartbeat(request):
        return web.json_response(
            {"result": [{"time": 1, "content": json.dumps({"user_cmd": {"run_cmd": "id"}})}]}
        )

    up, base = await _upstream(heartbeat)
    sessions = []
    client = await _proxying_client(base, sessions, block_run_cmd=False)
    try:
        r = await client.get("/6/poll/t5/heartbeat")
        assert "run_cmd" in await r.text()
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_proxy_unreachable_upstream_returns_502():
    sessions = []
    # Port 1 is reserved and never listening, so the connection fails fast.
    client = await _proxying_client("http://127.0.0.1:1", sessions)
    try:
        r = await client.get("/6/t5/dev_serverinfo")
        assert r.status == 502
        assert (await r.json())["result"] == {}
        # A failed request must not poison the pool for the next one.
        assert not sessions[0].closed
    finally:
        await close_proxy_session(client.app)
        await client.close()


# --- upstream selection -----------------------------------------------------

def test_normalize_upstream_drops_the_version_the_path_already_carries():
    """A device path already starts /6/, so a base ending /6/ builds /6/6/… —
    which upstream 404s and firmware then retries forever."""
    assert normalize_upstream("https://api-eu.petkt.com/6/") == "https://api-eu.petkt.com"
    assert normalize_upstream("https://api-eu.petkt.com/6") == "https://api-eu.petkt.com"
    assert normalize_upstream("https://api-eu.petkt.com/") == "https://api-eu.petkt.com"
    assert normalize_upstream("  https://api-eu.petkt.com  ") == "https://api-eu.petkt.com"


def test_normalize_upstream_leaves_a_path_that_merely_ends_in_six():
    """Only a whole `6` segment is a version, so /v6 and /api6 survive."""
    assert normalize_upstream("https://host/v6") == "https://host/v6"
    assert normalize_upstream("https://host/api6") == "https://host/api6"


def test_every_preset_normalizes_to_a_bare_host():
    for key in UPSTREAM_PRESETS:
        assert resolve_upstream(key).count("/") == 2  # scheme:// and nothing after


def test_resolve_upstream_presets_and_custom_urls():
    assert resolve_upstream("petkit-eu") == "https://api-eu.petkt.com"
    assert resolve_upstream("petkit-americas") == "https://api.petkt.com"
    assert resolve_upstream("petkit-asia") == "https://api.petktasia.com"
    assert resolve_upstream("petkit-cn") == "https://api.petkit.cn"
    assert resolve_upstream("petkit-ru") == "https://api-ru.petkit.cn"
    # A free-text URL is still accepted, so a setting saved by an older build
    # keeps working.
    assert resolve_upstream("https://my.mirror/6/") == "https://my.mirror"


def test_an_unset_upstream_falls_back_to_the_default_preset():
    """There is no per-device choice to make: provisioning decides the region,
    and nothing a device sends says which one it was given."""
    assert resolve_upstream("") == resolve_upstream(DEFAULT_UPSTREAM)
    assert resolve_upstream("   ") == resolve_upstream(DEFAULT_UPSTREAM)
    assert DEFAULT_UPSTREAM in UPSTREAM_PRESETS


async def test_proxy_request_builds_a_path_with_no_doubled_version():
    """End to end: the preset goes in, /6/6/ must not come out."""
    seen = []

    async def echo(request):
        seen.append(request.path)
        return web.json_response({"result": {}})

    up, base = await _upstream(echo)
    sessions = []
    # Pretend `base` was written the way the presets are.
    client = await _proxying_client(base + "/6/", sessions)
    try:
        await client.get("/6/t5/dev_state_report")
        assert seen == ["/6/t5/dev_state_report"]
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


# --- forward() --------------------------------------------------------------

async def _forwarding_client(base_url, policy, captured):
    """A client that forwards through `forward` and records the Exchange."""
    app = web.Application()

    async def handler(request: web.Request) -> web.Response:
        body = await request.read()
        exchange = await forward(request, body=body, upstream=base_url, policy=policy)
        captured.append(exchange)
        if exchange is None:
            return web.json_response({"local": True})
        return exchange.to_response()

    app.router.add_route("*", "/{path:.*}", handler)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _policy(**kw):
    device = Device(device_type="t5", petkit_id=10000001, serial_number="SN1")
    base = dict(device=device, api_url="http://192.0.2.199:8080/6/",
                mqtt_host="192.0.2.199", bucket_endpoint="https://192.0.2.199:9000",
                aes_key="0123456789abcdef")
    base.update(kw)
    return RedactionPolicy(**base)


async def test_forward_keeps_both_bodies_and_reports_the_redaction():
    """The whole point of proxy mode: what PetKit sent AND what the device got."""
    async def serverinfo(request):
        return web.json_response({"result": {"apiServers": ["https://api-eu.petkt.com/6/"],
                                             "ipServers": [], "dns": "223.5.5.5",
                                             "linked": 1, "nextTick": 300}})

    up, base = await _upstream(serverinfo)
    captured = []
    client = await _forwarding_client(base, _policy(), captured)
    try:
        r = await client.get("/6/t5/dev_serverinfo")
        body = await r.json()
        assert body["result"]["apiServers"] == ["http://192.0.2.199:8080/6/"]

        exchange = captured[0]
        assert b"api-eu.petkt.com" in exchange.upstream_body
        assert b"api-eu.petkt.com" not in exchange.body
        assert [rec.rule for rec in exchange.records] == ["server"]
        # A routine address rewrite is not an attempt, so nothing to persist.
        assert exchange.blocked == []
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_forward_returns_none_when_upstream_is_unreachable():
    """None means "we have nothing from PetKit" — the caller answers locally.
    `forward` never manufactures a 502; that was the device paying for the
    cloud's outage."""
    captured = []
    client = await _forwarding_client("http://127.0.0.1:1", _policy(), captured)
    try:
        r = await client.get("/6/t5/dev_serverinfo")
        assert r.status == 200
        assert (await r.json()) == {"local": True}
        assert captured == [None]
    finally:
        await close_proxy_session(client.app)
        await client.close()


async def test_forward_reports_a_blocked_attempt():
    async def hostile(request):
        return web.json_response(
            {"result": [{"time": 1,
                         "content": json.dumps({"user_cmd": {"run_cmd": "rm -rf /"}})}]})

    up, base = await _upstream(hostile)
    captured = []
    client = await _forwarding_client(base, _policy(), captured)
    try:
        r = await client.get("/6/poll/t5/heartbeat")
        assert (await r.json())["result"] == []
        assert [rec.rule for rec in captured[0].blocked] == ["rce"]
        assert captured[0].blocked[0].original == "rm -rf /"
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_forward_relays_the_upstream_status():
    async def gone(request):
        return web.json_response({"result": {}}, status=403)

    up, base = await _upstream(gone)
    captured = []
    client = await _forwarding_client(base, _policy(), captured)
    try:
        r = await client.get("/6/t5/dev_state_report")
        assert r.status == 403
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


# --- circuit breaker --------------------------------------------------------

async def test_breaker_opens_after_repeated_failures_and_stops_dialling():
    """A dead upstream must not add the full timeout to every device call for
    as long as proxy mode is left on."""
    captured = []
    client = await _forwarding_client("http://127.0.0.1:1", _policy(), captured)
    try:
        for _ in range(BREAKER_THRESHOLD):
            await client.get("/6/t5/dev_serverinfo")
        assert breaker_is_open(client.app)

        # Further calls short-circuit: still None, but without a dial attempt.
        await client.get("/6/t5/dev_serverinfo")
        assert captured == [None] * (BREAKER_THRESHOLD + 1)
    finally:
        await close_proxy_session(client.app)
        await client.close()


async def test_a_success_closes_the_breaker():
    async def ok(request):
        return web.json_response({"result": {}})

    up, base = await _upstream(ok)
    captured = []
    client = await _forwarding_client(base, _policy(), captured)
    try:
        await client.get("/6/t5/dev_state_report")
        assert not breaker_is_open(client.app)
    finally:
        await close_proxy_session(client.app)
        await client.close()
        await up.close()


async def test_closing_the_session_also_clears_the_breaker():
    app = web.Application()
    get_proxy_session(app)
    for _ in range(BREAKER_THRESHOLD):
        _record_outcome(app, ok=False)
    assert breaker_is_open(app)
    await close_proxy_session(app)
    assert not breaker_is_open(app)


# --- merge_json_result_lists ------------------------------------------------

def test_merge_puts_primary_entries_first():
    left = json.dumps({"result": [{"time": 1}]}).encode()
    right = json.dumps({"result": [{"time": 2}]}).encode()
    assert json.loads(merge_json_result_lists(left, right))["result"] == [{"time": 1}, {"time": 2}]


@pytest.mark.parametrize("left,right", [
    (b"not json", b'{"result": []}'),
    (b'{"result": []}', b"not json"),
    (b'{"result": {}}', b'{"result": []}'),
    (b'[1,2]', b'{"result": []}'),
])
def test_merge_refuses_shapes_it_cannot_join(left, right):
    """None rather than a silently mangled body — the caller picks a whole side."""
    assert merge_json_result_lists(left, right) is None
