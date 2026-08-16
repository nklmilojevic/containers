"""Panel API for the uploaded device logs: listing, grep, download, toggle.

There is no table behind these — the file is the record — so the listing walks
a directory the way `/api/capture` does.
"""
import os
import tempfile
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.web.hub import EventHub
from petkit_local.web.api.logs import MAX_LOG_LINE_CHARS, MAX_LOG_LINES
from petkit_local.web.panel import create_panel_app


def _panel(tmp, bucket_endpoint="https://192.0.2.199:9000"):
    reg = DeviceRegistry()
    device = reg.get_or_create(petkit_id=1, device_type="t5", serial_number="SN")
    log_root = str(Path(tmp) / "devicelogs")
    os.makedirs(log_root, exist_ok=True)
    cfg = {"api_url": "http://x/6/", "capture": False, "capture_dir": "/nope",
           "data_dir": tmp, "device_log_root": log_root,
           "bucket_endpoint": bucket_endpoint}
    app = create_panel_app(reg, None, EventHub(), cfg, bridge=None)
    return app, device, log_root


async def _client(app):
    c = TestClient(TestServer(app))
    await c.start_server()
    return c


def _write(log_root, rel, text):
    p = Path(log_root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


# --- listing ----------------------------------------------------------------

async def test_an_empty_tree_lists_nothing_and_explains_nothing_is_wrong():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, _root = _panel(tmp)
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs")).json()
            assert body["files"] == []
            assert body["reason"] == ""          # nothing is misconfigured
            assert body["enabled_devices"] == []  # it is just switched off
        finally:
            await c.close()


async def test_a_log_is_listed_with_the_device_from_its_directory():
    with tempfile.TemporaryDirectory() as tmp:
        app, device, root = _panel(tmp)
        device.config["log_upload_enabled"] = True
        _write(root, "1/1_312_devRun.log", "01-01 boot\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs")).json()
            assert len(body["files"]) == 1
            f = body["files"][0]
            assert f["device"] == 1
            assert f["rel"] == "1/1_312_devRun.log"
            assert f["size"] == len("01-01 boot\n")
            assert body["enabled_devices"] == [1]
        finally:
            await c.close()


async def test_a_bucket_address_that_cannot_be_split_is_reported_as_the_reason():
    """"Collection is on and nothing appears" is otherwise unanswerable from
    the UI, and this is one of the two silent ways to be in that state."""
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, _root = _panel(tmp, bucket_endpoint="https://localhost:9000")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs")).json()
            assert body["reason"] == "authority_not_splittable"
        finally:
            await c.close()


async def test_no_bucket_address_at_all_is_reported_separately():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, _root = _panel(tmp, bucket_endpoint="")
        c = await _client(app)
        try:
            assert (await (await c.get("/api/devicelogs")).json())["reason"] == "no_bucket_endpoint"
        finally:
            await c.close()


async def test_the_listing_can_be_narrowed_to_one_device():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/a.log", "a")
        _write(root, "2/b.log", "b")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs?device=2")).json()
            assert [f["rel"] for f in body["files"]] == ["2/b.log"]
        finally:
            await c.close()


# --- reading and grep -------------------------------------------------------

async def test_reading_returns_numbered_lines():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "alpha\nbravo\ncharlie\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log")).json()
            assert body["lines"] == [[1, "alpha"], [2, "bravo"], [3, "charlie"]]
            assert body["total"] == 3
            assert body["matched"] == 3
        finally:
            await c.close()


async def test_grep_filters_but_keeps_the_file_s_own_line_numbers():
    """A filtered view still has to say where in the log you are."""
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "alpha\nbravo\ncharlie\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log?q=ra")).json()
            assert body["lines"] == [[2, "bravo"]]
            assert body["matched"] == 1
            assert body["total"] == 3
        finally:
            await c.close()


async def test_grep_is_case_insensitive_and_ands_several_terms():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "ERROR mqtt connect\nERROR http timeout\ninfo mqtt ping\n")
        c = await _client(app)
        try:
            one = await (await c.get("/api/devicelogs/1/x.log?q=error")).json()
            assert one["matched"] == 2
            both = await (await c.get("/api/devicelogs/1/x.log?q=error+mqtt")).json()
            assert [n for n, _ in both["lines"]] == [1]
        finally:
            await c.close()


async def test_a_regex_is_treated_as_literal_text_not_compiled():
    """The panel is unauthenticated on the HTTPS port, so a caller-supplied
    pattern over a caller-supplied file would be a denial of service."""
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "aaaa\na+b\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log?q=a%2Bb")).json()
            assert [text for _, text in body["lines"]] == ["a+b"]
        finally:
            await c.close()


async def test_one_pathological_line_cannot_become_the_whole_response():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "z" * (MAX_LOG_LINE_CHARS * 3) + "\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log")).json()
            assert len(body["lines"][0][1]) == MAX_LOG_LINE_CHARS
        finally:
            await c.close()


async def test_the_line_limit_is_clamped_however_much_is_asked_for():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "".join(f"line {i}\n" for i in range(MAX_LOG_LINES + 500)))
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log?limit=999999")).json()
            assert len(body["lines"]) == MAX_LOG_LINES
        finally:
            await c.close()


async def test_offset_pages_through_the_matches():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "a1\nb\na2\n")
        c = await _client(app)
        try:
            body = await (await c.get("/api/devicelogs/1/x.log?q=a&offset=1")).json()
            assert body["lines"] == [[3, "a2"]]
        finally:
            await c.close()


async def test_a_traversing_path_is_not_found():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, _root = _panel(tmp)
        Path(tmp, "secret.txt").write_text("top secret")
        c = await _client(app)
        try:
            r = await c.get("/api/devicelogs/..%2Fsecret.txt")
            assert r.status == 404
            assert "top secret" not in await r.text()
        finally:
            await c.close()


async def test_download_serves_the_raw_file_as_an_attachment():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, root = _panel(tmp)
        _write(root, "1/x.log", "alpha\nbravo\n")
        c = await _client(app)
        try:
            r = await c.get("/api/devicelogs/1/x.log?download=1")
            assert r.status == 200
            assert "attachment" in r.headers["Content-Disposition"]
            assert await r.text() == "alpha\nbravo\n"
        finally:
            await c.close()


# --- the per-device toggle --------------------------------------------------

async def test_collection_is_off_until_it_is_switched_on():
    with tempfile.TemporaryDirectory() as tmp:
        app, device, _root = _panel(tmp)
        c = await _client(app)
        try:
            assert (await (await c.get("/api/devices/1/logs")).json())["log_upload_enabled"] is False

            r = await c.post("/api/devices/1/logs", json={"log_upload_enabled": True})
            assert (await r.json())["log_upload_enabled"] is True
            assert device.config["log_upload_enabled"] is True

            body = await (await c.get("/api/devices/1/logs")).json()
            assert body["log_upload_enabled"] is True
            assert body["reason"] == ""
        finally:
            await c.close()


async def test_the_toggle_reports_a_bad_id_and_an_unknown_device_distinctly():
    with tempfile.TemporaryDirectory() as tmp:
        app, _device, _root = _panel(tmp)
        c = await _client(app)
        try:
            assert (await c.get("/api/devices/nope/logs")).status == 400
            assert (await c.get("/api/devices/999/logs")).status == 404
        finally:
            await c.close()
