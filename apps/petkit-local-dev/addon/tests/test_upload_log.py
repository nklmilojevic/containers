"""dev_upload_log_token / dev_upload_log — the device's own debug log.

The shapes asserted here are not preferences: they come from 433 captured
exchanges with the real PetKit cloud (2026-07-28) and the strings in the
device's `logUpload` binary. See `http/handlers/upload_log.py`.
"""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from petkit_local.devices.base import split_bucket_authority
from petkit_local.devices.registry import DeviceRegistry
from petkit_local.http.server import create_app
from petkit_local.web.hub import EventHub

CONFIG = {"api_url": "http://x/6/", "bucket_endpoint": "https://192.0.2.199:9000"}


async def _client(enabled=False, config=None):
    reg = DeviceRegistry()
    device = reg.get_or_create(petkit_id=10000001, device_type="t5", serial_number="SN")
    device.config["log_upload_enabled"] = enabled
    hub = EventHub()
    app = create_app(reg, dict(config if config is not None else CONFIG))
    # The key `main.py` actually wires on the device-facing app. This used to
    # say `hub`, which production never sets — so the devlog assertion below
    # passed against a handler that could only ever see None.
    app["event_hub"] = hub
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, device, hub


# --- splitting our address into the pair the device concatenates back --------

@pytest.mark.parametrize("endpoint,expected", [
    ("https://192.0.2.199:9000", ("192", "0.2.199:9000")),
    ("https://192.0.2.199", ("192", "0.2.199")),
    ("https://box.lan:9000", ("box", "lan:9000")),
    ("http://a.b.c.d/ignored/path", ("a", "b.c.d")),
])
def test_the_two_halves_rejoin_into_our_own_address(endpoint, expected):
    """`logUpload` builds `https://{bucketName}.{endPoint}/...` from a single
    format string, so the only test that matters is that the device's own
    concatenation lands back on us."""
    assert split_bucket_authority(endpoint) == expected
    head, tail = expected
    assert f"{head}.{tail}" == endpoint.split("//", 1)[-1].split("/", 1)[0]


@pytest.mark.parametrize("endpoint", [
    "",                       # no Supervisor host-IP lookup: there is no default
    "https://localhost:9000",  # single label — nothing to cut at
    "https://[::1]:9000",      # IPv6 literal needs a URL rule of its own
    "https://u@host.lan",      # userinfo likewise
    "https://",
])
def test_an_address_that_cannot_be_split_yields_nothing(endpoint):
    assert split_bucket_authority(endpoint) is None


def test_the_cut_is_at_the_first_dot_so_the_bucket_name_has_none():
    """A real OSS bucket name may not contain a dot. The device rebuilds the
    same string either way, so the first-dot cut is chosen for the one case it
    changes: a firmware that sanity-checks `bucketName`."""
    bucket, _ = split_bucket_authority("https://192.0.2.199:9000")
    assert "." not in bucket


# --- the token endpoint -----------------------------------------------------

async def test_no_token_is_issued_until_the_device_is_switched_on():
    """Byte-identical to what this endpoint answered before local collection
    existed, so leaving it off is a no-op rather than a new behaviour."""
    client, _device, _hub = await _client(enabled=False)
    try:
        r = await client.get("/6/t5/dev_upload_log_token?id=10000001")
        assert await r.json() == {"result": {}}
    finally:
        await client.close()


async def test_a_switched_on_device_is_pointed_at_our_own_bucket():
    client, _device, _hub = await _client(enabled=True)
    try:
        body = await (await client.get("/6/t5/dev_upload_log_token?id=10000001")).json()
        result = body["result"]
        data = result["data"]

        # `type` is strcmp'd against "ali" to select the OSS branch.
        assert result["type"] == "ali"
        # What the device concatenates has to be our bucket, port included.
        assert f"{data['bucketName']}.{data['endPoint']}" == "192.0.2.199:9000"
        # The prefix is what routes the upload out of the media tree.
        assert data["pathPrefix"] == "devlog/10000001"
        # All six fields present: a field the firmware reads and does not find
        # is a strncpy from NULL, not a missing feature.
        assert set(data) == {"token", "bucketName", "pathPrefix", "endPoint", "secret", "keyId"}
        assert all(data.values())
        # No outer token: without it the Qiniu branch quits, which is the branch
        # we do not implement.
        assert "token" not in result
    finally:
        await client.close()


async def test_an_unidentified_caller_gets_no_token():
    """Unlike dev_oss_sts_info_new_v2, which serves a throwaway Device: there
    would be no petkit id to build a pathPrefix from, and a log that cannot be
    attributed is worse than no log."""
    client, _device, _hub = await _client(enabled=True)
    try:
        r = await client.get("/6/t5/dev_upload_log_token")
        assert await r.json() == {"result": {}}
    finally:
        await client.close()


async def test_a_bucket_address_we_cannot_split_declines_rather_than_lying():
    client, _device, _hub = await _client(
        enabled=True, config={"api_url": "http://x/6/", "bucket_endpoint": ""})
    try:
        r = await client.get("/6/t5/dev_upload_log_token?id=10000001")
        assert await r.json() == {"result": {}}
    finally:
        await client.close()


# --- the completion report --------------------------------------------------

async def test_the_upload_report_is_acknowledged_with_the_cloud_s_own_shape():
    """A bare STRING result, not the usual object — captured 2026-07-27."""
    client, _device, hub = await _client(enabled=True)
    try:
        r = await client.get(
            "/6/t5/dev_upload_log?deviceId=10000001&size=105716"
            "&key=https://192.0.2.199:9000/devlog/10000001/x_devRun.log"
            "&uploadres=ctrl_reboot")
        assert await r.json() == {"result": "success"}

        line = [e for e in hub.recent(10) if e["kind"] == "devlog"][0]
        assert line["device_id"] == 10000001
        assert "105716" in line["summary"]
        assert "ctrl_reboot" in line["summary"]
    finally:
        await client.close()


async def test_a_report_is_acknowledged_even_with_collection_off():
    """This is the last step of an upload the device has already finished.
    Telling it anything else only makes it retry what it cannot redo."""
    client, _device, _hub = await _client(enabled=False)
    try:
        r = await client.get("/6/t5/dev_upload_log?deviceId=10000001&size=1")
        assert await r.json() == {"result": "success"}
    finally:
        await client.close()


async def test_a_report_with_junk_in_its_query_never_fails():
    """Every value here is untrusted device input."""
    client, _device, _hub = await _client(enabled=True)
    try:
        r = await client.get("/6/t5/dev_upload_log?deviceId=NaN&size=huge&key=&uploadres=")
        assert r.status == 200
        assert await r.json() == {"result": "success"}
    finally:
        await client.close()


# --- proxy mode must not leak where the log went ----------------------------

def _proxy_request(key, bucket="https://192.0.2.199:9000"):
    from aiohttp.test_utils import make_mocked_request

    from petkit_local.http.middleware import _reports_a_local_log_upload
    req = make_mocked_request(
        "GET", "/6/t5/dev_upload_log?deviceId=1&size=9&key=" + key)
    return _reports_a_local_log_upload(req, {"bucket_endpoint": bucket})


def test_a_report_naming_our_own_bucket_is_not_forwarded_upstream():
    """The object URL rides in the QUERY STRING, and redaction only sanitises
    response bodies — so forwarding this would hand PetKit this add-on's LAN
    address and bucket layout while the guard was busy scrubbing the reply."""
    assert _proxy_request("https://192.0.2.199:9000/devlog/1/x_devRun.log") is True


def test_a_report_naming_petkit_s_own_oss_is_ordinary_proxied_traffic():
    """Keyed on where the object is, not on the endpoint: a device still
    uploading to PetKit reports a petkit URL, and that is worth forwarding."""
    assert _proxy_request(
        "https://petkit-storage-binary-prod-eu.oss-eu-central-1.aliyuncs.com/t5-log/x.log") is False


def test_with_no_bucket_of_our_own_nothing_is_special_cased():
    assert _proxy_request("https://192.0.2.199:9000/devlog/1/x.log", bucket="") is False


# --- and must not leak what the device recorded -----------------------------

def _guarded(endpoint, block=True):
    from aiohttp.test_utils import make_mocked_request

    from petkit_local.http.middleware import GUARDED_LOCAL_ENDPOINTS
    req = make_mocked_request("POST", f"/6/t5/{endpoint}")
    name = req.path.rstrip("/").rsplit("/", 1)[-1]
    return block and name in GUARDED_LOCAL_ENDPOINTS


def test_what_the_device_recorded_is_not_reported_to_petkit():
    """`dev_upload_file_info_v2` names every file the device just uploaded —
    its `eventId`, its module type, its AES IV and the pet/clean/toilet flags.
    Forwarded, that is a running account of what happened in somebody's home,
    sent to PetKit by a device its owner has taken off PetKit; the media itself
    goes to our bucket, so this metadata is the whole of what they would learn.

    Redaction cannot cover it — that rewrites response bodies, and by the time
    there is one the request has already been delivered. So the guard has to
    withhold the request, like `_reports_a_local_log_upload` does.
    """
    assert _guarded("dev_upload_file_info_v2") is True


def test_switching_the_guard_off_proxies_it_like_anything_else():
    """Not parked in LOCAL_ONLY_ENDPOINTS: that would put it out of proxy
    mode's reach for good, and proxy mode exists to be able to see traffic."""
    assert _guarded("dev_upload_file_info_v2", block=False) is False


def test_the_guard_does_not_reach_beyond_that_one_endpoint():
    for other in ("dev_state_report", "dev_event_report", "dev_signup"):
        assert _guarded(other) is False, other
