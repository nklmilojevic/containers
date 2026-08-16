"""Reading command output back off a device, and the free-space gate.

`run_cmd_capture` is the first thing in this project that can read a device
command's OUTPUT — `send_run_cmd` is fire-and-forget and `wait_for_heartbeat`
only reports that the queue drained. Since the transport has no delivery
acknowledgement, most of what is tested here is that a stale or partial result
cannot be mistaken for a fresh one.
"""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from petkit_local.devices.base import Device
from petkit_local.patchers.common import (
    SPACE_MARGIN_BYTES, WRAPPER_RESERVE_BYTES, InsufficientDeviceSpace,
    ensure_space_for, parse_df_available_bytes, parse_wc_c_sizes,
    required_free_bytes, run_cmd_capture,
)
from petkit_local.web.api.patchers import ALL_PATCHERS


def _device() -> Device:
    return Device(petkit_id=1, device_type="t5", serial_number="SN")


class _Files:
    """Stands in for the device's /tmp: what the probe httpd would serve."""

    def __init__(self):
        self.body: bytes | None = None


async def _serve(files: _Files):
    async def handler(request):
        if files.body is None:
            raise web.HTTPNotFound()
        return web.Response(body=files.body)

    app = web.Application()
    app.router.add_get("/{name}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


# --- the capture round-trip -------------------------------------------------

async def test_the_command_writes_atomically_and_only_then_serves_it():
    """Starting the httpd last means a 200 can only ever be a complete file."""
    d = _device()
    files = _Files()
    server = await _serve(files)
    port = server.port
    try:
        await run_cmd_capture(d, "127.0.0.1", "df -k", port=port,
                              timeout=0.2, poll_interval=0.05)
    finally:
        await server.close()

    sent = d.command_queue[0]
    assert "> /tmp/pk_" in sent and ".part 2>&1" in sent
    assert "mv /tmp/pk_" in sent
    # The rename precedes the server, not the other way round.
    assert sent.index("mv /tmp/pk_") < sent.index("busybox httpd")
    assert f"busybox httpd -p {port} -h /tmp" in sent


async def test_output_comes_back_with_the_sentinel_stripped():
    d = _device()
    files = _Files()
    server = await _serve(files)
    try:
        nonce_holder = []

        async def arm():
            # The nonce is only knowable from the command we just queued.
            while not d.command_queue:
                pass
            cmd = d.command_queue[0]
            nonce = cmd.split("__PK_END_")[1].split()[0]
            nonce_holder.append(nonce)
            files.body = f"Filesystem 1K-blocks\n/dev/root 100\n__PK_END_{nonce}\n".encode()

        import asyncio
        task = asyncio.create_task(arm())
        out = await run_cmd_capture(d, "127.0.0.1", "df -k", port=server.port,
                                    timeout=3, poll_interval=0.05)
        await task
    finally:
        await server.close()

    assert out is not None
    assert "__PK_END_" not in out
    assert out.endswith("/dev/root 100")


async def test_a_body_without_the_sentinel_is_treated_as_not_ready():
    """A partial write, or a leftover file from an earlier run, must read as
    'nothing yet' rather than as data."""
    d = _device()
    files = _Files()
    files.body = b"Filesystem 1K-blocks\n/dev/root 100\n"      # truncated: no sentinel
    server = await _serve(files)
    try:
        out = await run_cmd_capture(d, "127.0.0.1", "df -k", port=server.port,
                                    timeout=0.3, poll_interval=0.05)
    finally:
        await server.close()
    assert out is None


async def test_a_sentinel_from_a_different_run_is_rejected():
    d = _device()
    files = _Files()
    files.body = b"stale output\n__PK_END_deadbeefdeadbeef\n"
    server = await _serve(files)
    try:
        out = await run_cmd_capture(d, "127.0.0.1", "df -k", port=server.port,
                                    timeout=0.3, poll_interval=0.05)
    finally:
        await server.close()
    assert out is None


async def test_nothing_listening_returns_none_rather_than_raising():
    """A device-side failure is the caller's decision to make, not an
    exception that aborts a patcher mid-run."""
    d = _device()
    out = await run_cmd_capture(d, "127.0.0.1", "df -k", port=1,
                                timeout=0.2, poll_interval=0.05)
    assert out is None


async def test_the_output_file_is_cleaned_up_on_the_timeout_path_too():
    d = _device()
    await run_cmd_capture(d, "127.0.0.1", "df -k", port=1, timeout=0.2, poll_interval=0.05)
    assert any("rm -f /tmp/pk_" in c for c in d.command_queue)


async def test_two_calls_never_share_a_filename():
    """The property that makes a stale file unreadable by construction."""
    d = _device()
    for _ in range(2):
        await run_cmd_capture(d, "127.0.0.1", "df", port=1, timeout=0.05, poll_interval=0.01)
    names = {c.split("/tmp/")[1].split()[0].removesuffix(".part")
             for c in d.command_queue if "/tmp/pk_" in c}
    assert len(names) == 2


# --- parsing df and wc, host-side -------------------------------------------

BUSYBOX_DF = """\
Filesystem           1K-blocks      Used Available Use% Mounted on
/dev/root                10240     10240         0 100% /
tmpfs                    30516        76     30440   0% /tmp
/dev/mtdblock5            8192      3072      5120  38% /system
"""


def test_the_right_filesystem_is_picked_from_a_real_df_table():
    assert parse_df_available_bytes(BUSYBOX_DF, "/system") == 5120 * 1024
    assert parse_df_available_bytes(BUSYBOX_DF, "/tmp") == 30440 * 1024
    assert parse_df_available_bytes(BUSYBOX_DF, "/") == 0


def test_the_longest_matching_mountpoint_wins():
    """/system/foo lives on /system, not on /."""
    assert parse_df_available_bytes(BUSYBOX_DF, "/system/ctrl_patched") == 5120 * 1024


def test_a_wrapped_filesystem_name_does_not_shift_the_columns():
    """busybox puts a long device name on its own line — which is why the
    fields are counted from the right, not the left."""
    wrapped = ("Filesystem           1K-blocks      Used Available Use% Mounted on\n"
               "/dev/mmcblk0p7-with-a-very-long-name\n"
               "                          8192      3072      5120  38% /system\n")
    assert parse_df_available_bytes(wrapped, "/system") == 5120 * 1024


@pytest.mark.parametrize("text", [
    "", "df: /system: No such file or directory\n", "garbage\n", "Filesystem 1K-blocks\n",
])
def test_unparseable_output_is_unknown_not_zero(text):
    """None means 'we could not find out', which must never be confused with
    'the disk is full'."""
    assert parse_df_available_bytes(text, "/system") is None


def test_wc_output_maps_paths_to_sizes():
    text = "1420656 /system/ctrl_patched\n   1024 /system/app_init.sh\n1421680 total\n"
    assert parse_wc_c_sizes(text) == {
        "/system/ctrl_patched": 1420656, "/system/app_init.sh": 1024,
    }


def test_a_missing_file_is_simply_absent():
    """Nothing there is nothing to reclaim — not an error."""
    text = "wc: /system/nope: No such file or directory\n1024 /system/app_init.sh\n"
    assert parse_wc_c_sizes(text) == {"/system/app_init.sh": 1024}


# --- the gate ---------------------------------------------------------------

def test_the_requirement_credits_the_file_being_overwritten():
    """Without this, re-applying mqtt fails on exactly the devices where it is
    already installed and working."""
    fresh = required_free_bytes(1_420_656)
    reapply = required_free_bytes(1_420_656, existing_bytes=1_420_656)
    assert fresh > reapply
    assert reapply == WRAPPER_RESERVE_BYTES + SPACE_MARGIN_BYTES


def test_the_requirement_never_goes_negative():
    assert required_free_bytes(10, existing_bytes=10_000) == WRAPPER_RESERVE_BYTES + SPACE_MARGIN_BYTES


async def _gate(monkeypatch, captured):
    async def fake(device, ip, command, **kw):
        return captured
    monkeypatch.setattr("petkit_local.patchers.common.run_cmd_capture", fake)
    return await ensure_space_for(_device(), "127.0.0.1", write_bytes=1_000_000,
                                  targets=["/system/ctrl_patched"])


async def test_a_patch_that_does_not_fit_is_refused_with_both_numbers(monkeypatch):
    tight = BUSYBOX_DF.replace("      5120  38% /system", "        64  99% /system")
    with pytest.raises(InsufficientDeviceSpace) as e:
        await _gate(monkeypatch, tight)
    assert "free" in str(e.value) and "needs" in str(e.value)


async def test_a_patch_that_fits_reports_both_numbers_and_proceeds(monkeypatch):
    roomy = BUSYBOX_DF.replace("      5120  38% /system", "     51200  10% /system")
    msg = await _gate(monkeypatch, roomy)
    assert "/system" in msg and "free" in msg


async def test_an_unanswered_probe_warns_instead_of_blocking(monkeypatch):
    """Unknown is not evidence of danger. Refusing here would break patchers on
    a device whose busybox simply has no `df`."""
    async def fake(device, ip, command, **kw):
        return None
    monkeypatch.setattr("petkit_local.patchers.common.run_cmd_capture", fake)
    msg = await ensure_space_for(_device(), "127.0.0.1", write_bytes=1_000_000)
    assert msg.startswith("WARNING")


async def test_an_unparseable_probe_also_warns_instead_of_blocking(monkeypatch):
    msg = await _gate(monkeypatch, "df: applet not found\n")
    assert msg.startswith("WARNING")


# --- the registry contract --------------------------------------------------

def test_every_patcher_declares_more_space_than_the_gate_can_ever_demand():
    """A UI that promises less than the gate enforces would produce a refusal
    the user was told could not happen — so this fails for a new patcher that
    forgets the field, or sets it below the fixed overhead."""
    floor = WRAPPER_RESERVE_BYTES + SPACE_MARGIN_BYTES
    for pid, info in ALL_PATCHERS.items():
        assert info["needs_bytes"] >= floor, pid


@pytest.mark.parametrize("key", ["id", "name", "description", "files", "needs_bytes"])
def test_every_patcher_carries_the_full_metadata_the_panel_renders(key):
    for pid, info in ALL_PATCHERS.items():
        assert key in info, f"{pid} is missing {key}"
