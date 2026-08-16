import asyncio
import os
import time
import tempfile
import threading
from pathlib import Path

from petkit_local.events.store import EventStore
from petkit_local.media import retention
from petkit_local.media.retention import (DEFAULT_RETENTION, RetentionConfig, RetentionSweeper,
                                          sweep_all, sweep_category)


def test_retention_config_defaults():
    cfg = RetentionConfig()
    assert cfg.data == DEFAULT_RETENTION
    assert cfg.max_bytes("fullVideo") == 1024 * 1024 * 1024
    assert cfg.max_age_sec("fullVideo") == 7 * 86400


def test_retention_config_save_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RetentionConfig()
        cfg.update({"fullVideo": {"max_mb": 10, "max_days": 1}})
        cfg.save(tmp)

        loaded = RetentionConfig.load(tmp)
        assert loaded.data["fullVideo"] == {"max_mb": 10, "max_days": 1}
        assert loaded.data["eventImage"] == DEFAULT_RETENTION["eventImage"]  # untouched


def test_retention_config_save_leaves_no_partial_files():
    """Written temp-then-rename: a torn write used to leave a truncated
    retention.json, which `load` then silently read as "no overrides"."""
    with tempfile.TemporaryDirectory() as tmp:
        RetentionConfig().save(tmp)
        assert os.listdir(tmp) == ["retention.json"]


def test_retention_config_load_falls_back_on_corrupt_file():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "retention.json").write_text('{"fullVideo": {"max_mb": 1')  # truncated
        assert RetentionConfig.load(tmp).data == DEFAULT_RETENTION


def test_retention_config_load_ignores_non_object_json():
    """A valid-JSON-but-wrong-shape file used to raise AttributeError out of
    load() and take the whole add-on's startup down with it."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "retention.json").write_text('["fullVideo"]')
        assert RetentionConfig.load(tmp).data == DEFAULT_RETENTION


def test_retention_config_load_without_file_uses_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        assert RetentionConfig.load(tmp).data == DEFAULT_RETENTION


def test_retention_config_update_ignores_unknown_category():
    cfg = RetentionConfig()
    cfg.update({"notACategory": {"max_mb": 1}})
    assert "notACategory" not in cfg.data


def test_retention_config_update_zero_disables_cap():
    cfg = RetentionConfig()
    cfg.update({"fullVideo": {"max_mb": 0}})
    assert cfg.max_bytes("fullVideo") is None


async def _add_media(store, category, size, created_at, device_id=1, media_path=None):
    fid = f"f-{category}-{created_at}"
    await store.upsert_media({
        "file_id": fid, "device_id": device_id, "category": category,
        "status": "ready", "size_bytes": size, "media_path": media_path,
        # Backdated on INSERT: every age rule reads created_at, and nothing
        # may rewrite it afterwards.
        "created_at": created_at,
    })
    return fid


async def test_sweep_category_deletes_oldest_first_over_size_cap(event_store):
    store = event_store
    now = 1_000_000.0
    await _add_media(store, "fullVideo", 5 * 1024 * 1024, now - 300)
    await _add_media(store, "fullVideo", 5 * 1024 * 1024, now - 200)
    await _add_media(store, "fullVideo", 5 * 1024 * 1024, now - 100)

    cfg = RetentionConfig()
    cfg.update({"fullVideo": {"max_mb": 8, "max_days": None}})
    result = await sweep_category(store, "fullVideo", cfg, now=now)

    # 15MB total, 8MB cap: drop oldest twice (5MB, 5MB) -> 5MB, under cap.
    assert result["deleted"] == 2
    remaining = await store.media_for_retention("fullVideo")
    total = sum(m["size_bytes"] for m in remaining)
    assert total <= 8 * 1024 * 1024


async def test_sweep_category_deletes_past_age_cap_even_under_size(event_store):
    store = event_store
    now = 1_000_000.0
    old_id = await _add_media(store, "eventImage", 1024, now - (40 * 86400))
    await _add_media(store, "eventImage", 1024, now - 10)

    cfg = RetentionConfig()
    cfg.update({"eventImage": {"max_mb": None, "max_days": 30}})
    result = await sweep_category(store, "eventImage", cfg, now=now)

    assert result["deleted"] == 1
    remaining_ids = {m["file_id"] for m in await store.media_for_retention("eventImage")}
    assert old_id not in remaining_ids


async def test_sweep_category_noop_when_within_caps(event_store):
    store = event_store
    now = 1_000_000.0
    await _add_media(store, "highLight", 1024, now - 10)

    cfg = RetentionConfig()  # defaults: 512MB / 30 days
    result = await sweep_category(store, "highLight", cfg, now=now)
    assert result == {"deleted": 0, "freed_bytes": 0}
    assert len(await store.media_for_retention("highLight")) == 1


async def test_sweep_category_unlinks_the_expired_file_from_disk():
    """The row and the file go together — a sweep that only dropped rows would
    free no disk at all, which is the entire point of the sweeper."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(Path(tmp) / "petkit.db")
        now = 1_000_000.0
        doomed = Path(tmp) / "old.jpg"
        doomed.write_bytes(b"x" * 1024)
        await _add_media(store, "eventImage", 1024, now - (40 * 86400),
                         media_path=str(doomed))

        cfg = RetentionConfig()
        cfg.update({"eventImage": {"max_days": 30}})
        try:
            assert (await sweep_category(store, "eventImage", cfg, now=now))["deleted"] == 1
            assert not doomed.exists()
            assert await store.media_for_retention("eventImage") == []
        finally:
            await store.close()


async def test_sweep_category_drops_the_row_even_if_the_file_is_already_gone():
    """Otherwise every later sweep reconsiders the same unremovable row."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(Path(tmp) / "petkit.db")
        now = 1_000_000.0
        await _add_media(store, "eventImage", 1024, now - (40 * 86400),
                         media_path=os.path.join(tmp, "never-existed.jpg"))

        cfg = RetentionConfig()
        cfg.update({"eventImage": {"max_days": 30}})
        try:
            assert (await sweep_category(store, "eventImage", cfg, now=now))["deleted"] == 1
            assert await store.media_for_retention("eventImage") == []
        finally:
            await store.close()


async def test_sweep_all_prunes_events_by_age(event_store):
    store = event_store
    now = 1_000_000.0
    await store.upsert_event({"device_id": 1, "event_type": "old", "ts": now - (200 * 86400)})
    await store.upsert_event({"device_id": 1, "event_type": "recent", "ts": now - 10})

    cfg = RetentionConfig()
    cfg.update({"events": {"max_days": 180}})
    summary = await sweep_all(store, cfg, now=now)

    assert summary["events"]["removed"] == 1
    remaining = await store.query_timeline(device_id=1)
    assert len(remaining) == 1
    assert remaining[0]["event_type"] == "recent"


class _ThreadSpyStore:
    """Async store stub recording which thread each of its calls ran on."""

    def __init__(self, rows: list[dict] | None = None):
        self.threads: set[int] = set()
        self.deleted: list[int] = []
        self._rows = rows or []

    async def media_for_retention(self, category: str) -> list[dict]:
        self.threads.add(threading.get_ident())
        return [r for r in self._rows if r["category"] == category]

    async def delete_media(self, media_id: int) -> None:
        self.threads.add(threading.get_ident())
        self.deleted.append(media_id)

    async def prune_events(self, before_ts: float) -> int:
        self.threads.add(threading.get_ident())
        return 0

    async def prune_blocked_attempts(self, before_ts: float) -> int:
        self.threads.add(threading.get_ident())
        return 0


async def test_sweep_talks_to_the_store_on_the_loop_and_unlinks_off_it():
    """Split deliberately: the store is async, so its coroutines can only be
    driven by this loop (a `to_thread`'d sweep would hand back un-awaited
    coroutines and silently delete nothing). Unlinking hundreds of expired
    files is the blocking part, so that alone goes to a thread."""
    rows = [{"id": 7, "category": "fullVideo", "media_path": "/x/old.mp4",
             "size_bytes": 10, "created_at": 0.0}]
    store = _ThreadSpyStore(rows)
    cfg = RetentionConfig()
    cfg.update({"fullVideo": {"max_days": 1}})

    unlink_threads: list[int] = []
    unlinked: list[str] = []
    original = retention._unlink_all

    def spy(paths: list[str]) -> None:
        unlink_threads.append(threading.get_ident())
        unlinked.extend(paths)

    retention._unlink_all = spy
    try:
        summary = await sweep_all(store, cfg, now=86400.0 * 3)
    finally:
        retention._unlink_all = original

    assert summary["fullVideo"]["deleted"] == 1
    assert unlinked == ["/x/old.mp4"]
    assert unlink_threads and threading.get_ident() not in unlink_threads, \
        "the unlinks ran on the event loop's thread"
    assert store.threads == {threading.get_ident()}, "store calls must stay on the loop"
    assert store.deleted == [7]


async def test_retention_sweeper_run_loops_without_a_thread_hand_off():
    """`RetentionSweeper.run` awaits the sweep directly now; handing it to
    `asyncio.to_thread` would produce coroutines nobody awaits."""
    store = _ThreadSpyStore()
    sweeper = RetentionSweeper(store, RetentionConfig(), interval=3600.0)
    task = asyncio.create_task(sweeper.run())
    for _ in range(500):
        await asyncio.sleep(0.002)
        if store.threads:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert store.threads, "the sweeper never ran a sweep"


def test_retention_config_update_ignores_non_numeric_value():
    # `update` is fed the raw JSON body of POST /api/retention, which
    # web/api/settings.py::api_retention does not validate. A bare float() raised
    # ValueError here -> 500, with earlier fields in the same request already
    # applied because the merge mutates in place.
    cfg = RetentionConfig()
    before = cfg.max_bytes("fullVideo")
    cfg.update({"fullVideo": {"max_mb": "not a number"}})
    assert cfg.data["fullVideo"]["max_mb"] is None
    assert cfg.max_bytes("fullVideo") is None
    assert before is not None  # it really was set before the bad patch

    cfg2 = RetentionConfig()
    cfg2.update({"fullVideo": {"max_days": ["nope"]}})
    assert cfg2.data["fullVideo"]["max_days"] is None


def test_retention_config_update_rejects_non_finite_and_negative():
    # float("inf") is ACCEPTED by bare float(), which is worse than raising:
    # it is persisted to retention.json and only detonates later, inside the
    # sweeper, at int(mb * 1024 * 1024) -> OverflowError.
    cfg = RetentionConfig()
    cfg.update({"fullVideo": {"max_mb": "inf", "max_days": "nan"}})
    assert cfg.data["fullVideo"] == {"max_mb": None, "max_days": None}
    assert cfg.max_bytes("fullVideo") is None
    assert cfg.max_age_sec("fullVideo") is None

    # A negative cap must read as "no cap" (what 0 already means), never as
    # "prune everything" — which is how max_bytes would have used it.
    cfg.update({"eventImage": {"max_mb": -5}})
    assert cfg.max_bytes("eventImage") is None


def test_retention_config_update_accepts_numeric_strings():
    # The panel posts form values, so the numbers legitimately arrive as text.
    cfg = RetentionConfig()
    cfg.update({"fullVideo": {"max_mb": "10", "max_days": " 2 "}})
    assert cfg.max_bytes("fullVideo") == 10 * 1024 * 1024
    assert cfg.max_age_sec("fullVideo") == 2 * 86400


# --- device logs ------------------------------------------------------------
# Uploaded device logs have no database row: the file IS the record, so this
# sweep walks a directory instead of a table. It still has to enforce BOTH caps
# the way sweep_category does, which is why the decision loop is shared.

def _write_log(root, name, size, age_days=0):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
    return str(path)


def test_the_device_log_category_is_capped_for_the_rate_measured_on_hardware():
    """~36 MB/day: the device uploads on every ~5-minute poll once a token is
    always available. The size cap is the one that bites; the age cap is a
    backstop for a device that goes quiet."""
    assert DEFAULT_RETENTION[retention.DEVICE_LOG_CATEGORY] == {"max_mb": 18, "max_days": 1}


def test_the_device_log_category_is_not_swept_as_media():
    """It has no `media` rows, so sweep_category would find nothing and the
    real files would live forever."""
    assert retention.DEVICE_LOG_CATEGORY not in retention.MEDIA_CATEGORIES


async def test_the_size_cap_drops_the_oldest_logs_first():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RetentionConfig()
        cfg.update({retention.DEVICE_LOG_CATEGORY: {"max_mb": 0.001, "max_days": None}})  # 1024 B
        old = _write_log(tmp, "1/old.log", 800, age_days=2)
        new = _write_log(tmp, "1/new.log", 800)

        out = await retention.sweep_device_logs(tmp, cfg)

        assert out["deleted"] == 1
        assert not os.path.exists(old)
        assert os.path.exists(new)
        assert out["freed_bytes"] == 800


async def test_the_age_cap_drops_an_old_log_however_small_the_tree_is():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = RetentionConfig()
        cfg.update({retention.DEVICE_LOG_CATEGORY: {"max_mb": None, "max_days": 3}})
        stale = _write_log(tmp, "1/stale.log", 10, age_days=9)
        fresh = _write_log(tmp, "1/fresh.log", 10)

        out = await retention.sweep_device_logs(tmp, cfg)

        assert out["deleted"] == 1
        assert not os.path.exists(stale)
        assert os.path.exists(fresh)


async def test_nothing_is_deleted_while_both_caps_are_satisfied():
    with tempfile.TemporaryDirectory() as tmp:
        keep = _write_log(tmp, "1/keep.log", 100)
        out = await retention.sweep_device_logs(tmp, RetentionConfig())
        assert out == {"deleted": 0, "freed_bytes": 0}
        assert os.path.exists(keep)


async def test_a_missing_log_root_is_not_an_error():
    """The sweeper is the only thing between a chatty device and a full disk,
    so it must survive a tree that is absent or was never configured."""
    assert await retention.sweep_device_logs("", RetentionConfig()) == {"deleted": 0, "freed_bytes": 0}
    assert await retention.sweep_device_logs("/no/such/dir", RetentionConfig()) == {
        "deleted": 0, "freed_bytes": 0}


async def test_sweep_all_still_works_without_a_log_root(event_store):
    """Both existing call sites pass two arguments."""
    summary = await sweep_all(event_store, RetentionConfig())
    assert summary[retention.DEVICE_LOG_CATEGORY] == {"deleted": 0, "freed_bytes": 0}
