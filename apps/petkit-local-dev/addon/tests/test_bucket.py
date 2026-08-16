import hashlib
import os
import tempfile

from aiohttp.test_utils import TestClient, TestServer
from yarl import URL

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.http.bucket import create_bucket_app
from petkit_local.web.hub import EventHub


async def _client(media_root, hub=None, log_root=None, registry=None):
    app = create_bucket_app(media_root, hub=hub, log_root=log_root, registry=registry)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _registry(enabled=True, petkit_id=1):
    """A registry holding one device, with log collection on or off."""
    reg = DeviceRegistry()
    d = reg.get_or_create(petkit_id=petkit_id, device_type="t5", serial_number="SN")
    d.config["log_upload_enabled"] = enabled
    return reg


def _traversal(key):
    """A URL whose dots survive to the handler.

    It is yarl, i.e. the *client* side, that normalises a literal `/../` away —
    the server does not. A raw `PUT /../../x HTTP/1.1` on the wire reaches
    match_info as `../../x` verbatim, which is exactly what a real attacker
    sends and what the handler's guard has to stop. Percent-encoding the dots is
    only how we stop our own test client from sanitising the attack first.
    """
    return URL("/" + key.replace(".", "%2e"), encoded=True)


async def test_put_then_get_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            r = await client.put("/t5/1/eventImage/somekey", data=b"hello world")
            assert r.status == 200
            assert "ETag" in r.headers

            r2 = await client.get("/t5/1/eventImage/somekey")
            assert r2.status == 200
            assert await r2.read() == b"hello world"
        finally:
            await client.close()


async def test_get_missing_key_is_404():
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            r = await client.get("/no/such/key")
            assert r.status == 404
        finally:
            await client.close()


async def test_put_notifies_hub_with_device_id_parsed_from_path():
    with tempfile.TemporaryDirectory() as tmp:
        hub = EventHub()
        client = await _client(tmp, hub=hub)
        try:
            await client.put("/t5/42/eventImage/somekey", data=b"x")
            events = hub.recent(10)
            assert len(events) == 1
            assert events[0]["kind"] == "media"
            assert events[0]["device_id"] == 42
        finally:
            await client.close()


async def test_put_without_hub_does_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp, hub=None)
        try:
            r = await client.put("/t5/1/eventImage/somekey", data=b"x")
            assert r.status == 200
        finally:
            await client.close()


async def test_put_cannot_escape_media_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "raw")
        os.makedirs(root)
        client = await _client(root)
        try:
            r = await client.put(_traversal("../../escaped.txt"), data=b"pwned")
            assert r.status == 403
        finally:
            await client.close()
        assert not os.path.exists(os.path.join(tmp, "escaped.txt"))
        assert os.listdir(root) == []


async def test_post_cannot_escape_media_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "raw")
        os.makedirs(root)
        client = await _client(root)
        try:
            r = await client.post(_traversal("../escaped.txt"), data=b"pwned")
            assert r.status == 403
        finally:
            await client.close()
        assert not os.path.exists(os.path.join(tmp, "escaped.txt"))


async def test_get_cannot_read_outside_media_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "raw")
        os.makedirs(root)
        with open(os.path.join(tmp, "secret.txt"), "wb") as f:
            f.write(b"top secret")
        client = await _client(root)
        try:
            r = await client.get(_traversal("../secret.txt"))
            assert r.status == 403
            assert b"top secret" not in await r.read()

            r2 = await client.head(_traversal("../secret.txt"))
            assert r2.status == 403
        finally:
            await client.close()


async def test_absolute_looking_key_stays_under_media_root():
    # The device legitimately PUTs keys with a leading slash; they must land
    # inside the root, not at the filesystem root.
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            # %2f so the leading slash survives routing into match_info.
            r = await client.put(URL("/%2ft5/1/fullVideo/chunk0", encoded=True), data=b"v")
            assert r.status == 200
        finally:
            await client.close()
        assert os.path.isfile(os.path.join(tmp, "t5", "1", "fullVideo", "chunk0"))


async def test_nested_key_creates_directories_and_head_reports_size():
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            r = await client.put("/t5/42/fullVideo/2026-07-26/chunk-0.ts", data=b"abcdef")
            assert r.status == 200

            r2 = await client.head("/t5/42/fullVideo/2026-07-26/chunk-0.ts")
            assert r2.status == 200
            assert r2.headers["Content-Length"] == "6"
        finally:
            await client.close()
        dest = os.path.join(tmp, "t5", "42", "fullVideo", "2026-07-26", "chunk-0.ts")
        with open(dest, "rb") as f:
            assert f.read() == b"abcdef"


async def test_etag_is_a_stable_content_digest():
    # hash() of bytes is salted per process, so the old ETag changed on every
    # restart — a cache validator must not.
    body = b"the same object bytes"
    expected = '"%s"' % hashlib.md5(body).hexdigest().upper()
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            first = await client.put("/t5/1/eventImage/k", data=body)
            second = await client.put("/t5/1/eventImage/k", data=body)
            other = await client.put("/t5/1/eventImage/k2", data=b"different bytes")

            assert first.headers["ETag"] == second.headers["ETag"] == expected
            assert other.headers["ETag"] != expected
        finally:
            await client.close()


async def test_put_to_the_bucket_root_is_refused_and_stages_nothing_outside():
    # `PUT /` resolves to media_root itself. atomic_write_bytes stages its temp
    # file in the target's PARENT, so accepting this would write the whole
    # unauthenticated body OUTSIDE the media root before the rename failed --
    # and answer 500, which the device retries forever.
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "raw")
        os.makedirs(root)
        client = await _client(root)
        try:
            for key in ("/", "/.", "//", URL("/%2e", encoded=True)):
                r = await client.put(key, data=b"pwned")
                assert r.status == 403, f"{key!r} -> {r.status}"
                r2 = await client.post(key, data=b"pwned")
                assert r2.status == 403, f"{key!r} -> {r2.status}"
        finally:
            await client.close()
        # nothing staged next to the root, and the root is untouched
        assert os.listdir(tmp) == ["raw"]
        assert os.listdir(root) == []


async def test_put_over_an_existing_directory_is_refused_not_a_500():
    # A trailing slash, or a key that is a prefix of one already stored, makes
    # the key name a directory. No write can satisfy it, so the answer has to be
    # terminal (403) -- a 5xx would loop the device's cloud process forever.
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            assert (await client.put("/t5/1/fullVideo/chunk0", data=b"v")).status == 200

            for key in ("/t5/1/fullVideo/", "/t5/1/fullVideo", "/t5"):
                r = await client.put(key, data=b"pwned")
                assert r.status == 403, f"{key!r} -> {r.status}"
                r2 = await client.post(key, data=b"pwned")
                assert r2.status == 403, f"{key!r} -> {r2.status}"

            # the real object is still intact
            got = await client.get("/t5/1/fullVideo/chunk0")
            assert await got.read() == b"v"
        finally:
            await client.close()


async def test_get_root_lists_bucket():
    with tempfile.TemporaryDirectory() as tmp:
        client = await _client(tmp)
        try:
            r = await client.get("/")
            assert r.status == 200
            assert "ListBucketResult" in await r.text()
        finally:
            await client.close()


# --- device logs ------------------------------------------------------------
# The bucket serves two unrelated things on one unauthenticated listener. These
# pin the boundary between them: a device log must never land in the media tree
# (media/pipeline.py locates raw files by substring and DELETES what it finds),
# and a media upload must be unaffected by the log root existing.

async def test_a_device_log_lands_under_the_log_root_and_not_in_media():
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as logs:
        client = await _client(media, log_root=logs, registry=_registry())
        try:
            r = await client.put("/devlog/1/1_312_devRun.log", data=b"01-01 boot\n")
            assert r.status == 200
            assert os.path.isfile(os.path.join(logs, "1", "1_312_devRun.log"))
            # Nothing at all may appear in the media tree.
            assert not any(os.scandir(media))
        finally:
            await client.close()


async def test_a_device_log_is_refused_when_that_device_has_collection_off():
    """The token is not the only gate. Switching collection off has to take
    effect before the token we already handed out would have expired."""
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as logs:
        client = await _client(media, log_root=logs, registry=_registry(enabled=False))
        try:
            r = await client.put("/devlog/1/x.log", data=b"nope")
            assert r.status == 403
            assert not any(os.scandir(logs))
        finally:
            await client.close()


async def test_a_device_log_is_refused_when_no_log_root_is_configured():
    with tempfile.TemporaryDirectory() as media:
        client = await _client(media, registry=_registry())
        try:
            assert (await client.put("/devlog/1/x.log", data=b"nope")).status == 403
            assert not any(os.scandir(media))
        finally:
            await client.close()


async def test_a_device_log_key_still_cannot_escape_its_root():
    """Stripping the prefix must not cost the containment check."""
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as logs:
        client = await _client(media, log_root=logs, registry=_registry())
        try:
            r = await client.put(_traversal("devlog/1/../../../escape.log"), data=b"x")
            assert r.status == 403
            assert not os.path.exists(os.path.join(os.path.dirname(logs), "escape.log"))
        finally:
            await client.close()


async def test_a_key_naming_the_log_root_itself_is_refused_and_stages_nothing():
    """The write-root check must compare against the root the key ROUTED to.
    Checking only the media root would let this stage the whole body in the log
    root's parent — which is /data, next to petkit.db."""
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as parent:
        logs = os.path.join(parent, "devicelogs")
        os.makedirs(logs)
        client = await _client(media, log_root=logs, registry=_registry())
        try:
            for key in ("/devlog", "/devlog/", "/devlog/."):
                assert (await client.put(key, data=b"x" * 1024)).status == 403
            assert os.listdir(parent) == ["devicelogs"]
            assert not any(os.scandir(logs))
        finally:
            await client.close()


async def test_media_uploads_are_unaffected_by_a_configured_log_root():
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as logs:
        client = await _client(media, log_root=logs, registry=_registry())
        try:
            assert (await client.put("/t5/1/eventImage/k", data=b"jpeg")).status == 200
            assert os.path.isfile(os.path.join(media, "t5", "1", "eventImage", "k"))
            assert not any(os.scandir(logs))
        finally:
            await client.close()


async def test_the_log_prefix_can_never_collide_with_a_device_codename():
    """A media key begins with a codename, so the routing prefix must not be
    one — otherwise that model's uploads would be diverted into the log tree."""
    from petkit_local.utils.const import DEVICE_LOG_KEY_PREFIX, DEVICE_NAMES

    assert DEVICE_LOG_KEY_PREFIX not in DEVICE_NAMES


async def test_a_received_log_is_announced_to_the_panel_with_its_device():
    with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as logs:
        hub = EventHub()
        client = await _client(media, hub=hub, log_root=logs, registry=_registry(petkit_id=7))
        try:
            await client.put("/devlog/7/7_1_devRun.log", data=b"line")
            ev = [e for e in hub.recent(10) if e["kind"] == "devlog"]
            assert len(ev) == 1
            assert ev[0]["device_id"] == 7
        finally:
            await client.close()
