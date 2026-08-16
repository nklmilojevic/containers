"""Nothing may grow forever, and no subprocess may run forever.

Three stores had no pruning at all (`.raw` staging, the thumbnail cache, the
hub's per-device diagnostics) and seven ffmpeg call sites had no deadline. Each
is slow-burning rather than dramatic, which is exactly why they need a test —
nobody notices until the disk is full or a task has been wedged for a week.
"""
import asyncio
import os
import time
from pathlib import Path

import pytest

from petkit_local.media import transcode
from petkit_local.media.retention import (
    DEFAULT_RETENTION, RAW_CATEGORY, RAW_MIN_AGE_SEC, THUMBNAIL_CATEGORY, RetentionConfig,
    sweep_directory,
)
from petkit_local.web.hub import MAX_TRACKED_DEVICES, MAX_TRACKED_OUTCOMES, EventHub


def _file(root: Path, name: str, size: int, age_sec: float) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    when = time.time() - age_sec
    os.utime(p, (when, when))
    return p


# --- the staging directory --------------------------------------------------

async def test_orphaned_staged_uploads_are_swept_by_age(tmp_path):
    """A raw upload whose `dev_upload_file_info_v2` never arrived — device
    rebooted mid-visit, capability toggled off between the PUT and the report —
    used to stay forever, because the pipeline only deletes on success."""
    cfg = RetentionConfig()
    cfg.update({RAW_CATEGORY: {"max_mb": None, "max_days": 1}})
    old = _file(tmp_path, "t5/1/fullVideo/old.ts", 100, age_sec=3 * 86400)
    recent = _file(tmp_path, "t5/1/fullVideo/recent.ts", 100, age_sec=2 * 3600)

    out = await sweep_directory(str(tmp_path), RAW_CATEGORY, cfg, min_age=RAW_MIN_AGE_SEC)
    assert out["deleted"] == 1
    assert not old.exists() and recent.exists()


async def test_the_age_floor_protects_a_file_a_running_job_may_still_be_writing(tmp_path):
    """`.raw` is ALSO the stitcher's work_dir, and holds uploads legitimately
    waiting for their metadata. A size cap will happily evict the NEWEST file
    when the tree is over budget, so without a floor the sweep could delete a
    file mid-write. This is the single most important assertion in this file."""
    cfg = RetentionConfig()
    # A cap so small that everything is over budget.
    cfg.update({RAW_CATEGORY: {"max_mb": 0.0001, "max_days": None}})
    fresh = _file(tmp_path, "stitching_abc.mp4", 5000, age_sec=30)          # in flight
    older = _file(tmp_path, "t5/1/fullVideo/stale.ts", 5000, age_sec=5 * 3600)

    out = await sweep_directory(str(tmp_path), RAW_CATEGORY, cfg, min_age=RAW_MIN_AGE_SEC)
    assert fresh.exists(), "the sweep deleted a file young enough to be in flight"
    assert not older.exists()
    # freed_bytes must count what was actually unlinked, not what the cap wanted.
    assert out["freed_bytes"] == 5000


async def test_freed_bytes_reports_only_what_was_really_deleted(tmp_path):
    cfg = RetentionConfig()
    cfg.update({RAW_CATEGORY: {"max_mb": 0.0001, "max_days": None}})
    _file(tmp_path, "young.ts", 999, age_sec=10)
    out = await sweep_directory(str(tmp_path), RAW_CATEGORY, cfg, min_age=RAW_MIN_AGE_SEC)
    assert out == {"deleted": 0, "freed_bytes": 0}


# --- the thumbnail cache ----------------------------------------------------

async def test_orphaned_thumbnails_are_swept(tmp_path):
    """Thumbnails are keyed by a hash of the video path, so when retention
    deletes the clip the thumbnail is unreachable forever. They are a pure
    cache, so evicting one costs a single re-render — no age floor needed."""
    cfg = RetentionConfig()
    cfg.update({THUMBNAIL_CATEGORY: {"max_mb": None, "max_days": 7}})
    old = _file(tmp_path, "deadbeef.jpg", 2000, age_sec=30 * 86400)
    new = _file(tmp_path, "cafebabe.jpg", 2000, age_sec=60)

    out = await sweep_directory(str(tmp_path), THUMBNAIL_CATEGORY, cfg)
    assert out["deleted"] == 1
    assert not old.exists() and new.exists()


async def test_a_missing_directory_is_not_an_error(tmp_path):
    """Both roots are optional — a build with no media dir must not crash the
    sweeper, which is the only thing standing between a device and a full disk."""
    cfg = RetentionConfig()
    assert await sweep_directory(str(tmp_path / "nope"), RAW_CATEGORY, cfg) == {
        "deleted": 0, "freed_bytes": 0}
    assert await sweep_directory("", RAW_CATEGORY, cfg) == {"deleted": 0, "freed_bytes": 0}


#: Retention categories deliberately absent from the panel, and why.
#:
#: Only `rawUpload`. Staged uploads are already deleted twice without anyone
#: asking — inline by `media/pipeline.py` the moment an upload is filed, and by
#: the sweeper for orphans whose metadata never arrived, under a code-level
#: `RAW_MIN_AGE_SEC` floor a panel edit cannot lower. Exposing it offered a
#: knob for a failure mode the user cannot observe, over a hidden directory no
#: screen in this app can browse, while `wasteCheck` and `healthPic` — photo
#: galleries the Timeline actually renders — had no control at all.
NOT_IN_PANEL = {RAW_CATEGORY}


def test_every_retention_category_is_editable_from_the_panel():
    """`RETENTION_LABELS` in setup.js is the single source for the retention table
    AND its save payload, so a category missing from it renders no row and can
    never be changed.

    Checks the WHOLE server-side table, not a hand-listed pair: the point is to
    catch a category added to `DEFAULT_RETENTION` and then forgotten in the UI,
    which is how `wasteCheck` and `healthPic` stayed unreachable. Dropping one
    on purpose means naming it in `NOT_IN_PANEL` with the reason.
    """
    js = Path(__file__).resolve().parents[1] / "petkit_local/web/static/js/setup.js"
    text = js.read_text()
    labels = text[text.index("const RETENTION_LABELS = {"):]
    labels = labels[: labels.index("};")]

    # `events` and `blocked` are age-only, so they get their own hardcoded rows
    # rather than RETENTION_LABELS entries (which also carry a size cap).
    age_only = {"events", "blocked"}
    for c in age_only:
        assert f"ret_{c}_days" in text, f"{c} has no age input in the panel"
    expected = {c for c in DEFAULT_RETENTION if c not in NOT_IN_PANEL and c not in age_only}
    missing = sorted(c for c in expected if f"{c}:" not in labels)
    assert not missing, f"no panel row, so these caps can never be changed: {missing}"

    stale = sorted(c for c in NOT_IN_PANEL if f"{c}:" in labels)
    assert not stale, f"listed as deliberately absent but present in the panel: {stale}"


# --- the hub's per-device diagnostics ---------------------------------------

def test_diagnostics_cannot_grow_without_bound():
    """The key is a device id from the `X-Device` header, on an API that binds
    0.0.0.0:80 and requires no registration — so anything on the LAN could add
    an entry per id just by looping curl."""
    hub = EventHub()
    for i in range(MAX_TRACKED_DEVICES * 3):
        hub.record_http(i, "GET", "/6/t5/dev_serverinfo", 200)
    assert len(hub._diag) <= MAX_TRACKED_DEVICES


def test_the_device_you_are_watching_is_the_one_that_survives():
    """Eviction is least-recently-touched, so a real device stays even while an
    id sweep runs against it."""
    hub = EventHub()
    hub.record_http(999, "GET", "/6/t5/dev_serverinfo", 200)
    for i in range(MAX_TRACKED_DEVICES * 2):
        hub.record_http(i, "GET", "/6/t5/dev_serverinfo", 200)
        hub.record_http(999, "GET", "/6/t5/dev_serverinfo", 200)  # still active
    assert hub.diag(999)["http_count"] > 0


def test_upstream_outcome_counters_are_bounded_too():
    """`error_<code>` is built from an upstream-controlled response body."""
    hub = EventHub()
    for i in range(MAX_TRACKED_OUTCOMES * 3):
        hub.record_upstream(f"error_{i}")
    assert len(hub.upstream_counts()) <= MAX_TRACKED_OUTCOMES


# --- ffmpeg deadlines -------------------------------------------------------

async def test_a_hung_ffmpeg_is_killed_and_reaped():
    """The point is not that the call returns — `wait_for` cancels the AWAIT,
    not the process, so a naive timeout leaves the child running and holding
    its files. Assert the process is actually gone."""
    rc, _, _ = await transcode.run_ffmpeg(
        ["sleep", "60"], timeout=0.3, what="a wedged encode")
    assert rc == -2  # timed out


async def test_the_child_really_exits_when_the_deadline_fires():
    proc_holder = {}
    real_exec = asyncio.create_subprocess_exec

    async def capture(*args, **kw):
        proc = await real_exec(*args, **kw)
        proc_holder["proc"] = proc
        return proc

    asyncio.create_subprocess_exec = capture
    try:
        await transcode.run_ffmpeg(["sleep", "60"], timeout=0.3, what="x")
    finally:
        asyncio.create_subprocess_exec = real_exec
    proc = proc_holder["proc"]
    assert proc.returncode is not None, "the child outlived its deadline"


async def test_a_cancelled_run_also_kills_the_child():
    """The stitcher's 30s shutdown cancel could not previously terminate a
    running ffmpeg, so restarting mid-stitch orphaned it."""
    task = asyncio.create_task(
        transcode.run_ffmpeg(["sleep", "60"], timeout=60, what="x"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_missing_binary_is_a_failure_not_an_exception():
    rc, _, _ = await transcode.run_ffmpeg(
        ["definitely-not-a-real-binary-xyz"], timeout=5, what="x")
    assert rc == -1


async def test_a_normal_command_still_returns_its_output():
    rc, stdout, _ = await transcode.run_ffmpeg(
        ["echo", "hello"], timeout=10, what="x")
    assert rc == 0 and b"hello" in stdout
