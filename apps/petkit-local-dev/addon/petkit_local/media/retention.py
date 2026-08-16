"""Retention sweeper — nothing else prunes media or events, so without this
the disk fills up unbounded. Enforces per-capability size + age caps on
media (oldest-first), and an age cap on events, on a background timer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from petkit_local.utils.coerce import to_float
from petkit_local.utils.jsonio import atomic_write_json, read_json

if TYPE_CHECKING:
    from petkit_local.events.store import EventStore

log = logging.getLogger(__name__)

RETENTION_FILENAME = "retention.json"

# Capability categories mirror devices/base.py::Device.CAPABILITY_TYPES.
DEFAULT_RETENTION = {
    "fullVideo": {"max_mb": 1024, "max_days": 7},
    "eventImage": {"max_mb": 512, "max_days": 30},
    "highLight": {"max_mb": 512, "max_days": 30},
    "dynamicVideo": {"max_mb": 512, "max_days": 7},
    # The low-res substream duplicates the main recording's timespan, so it
    # gets a tighter budget by default — see events/ingest.py's
    # _MODULE_TYPE_TO_CATEGORY for what it actually is.
    "cloudDouble": {"max_mb": 256, "max_days": 3},
    # The "Check waste" galleries (~5 small JPEGs per cleaning).
    "wasteCheck": {"max_mb": 512, "max_days": 30},
    # Stool-health-analysis photos (HEALTH_PRED).
    "healthPic": {"max_mb": 256, "max_days": 90},
    "events": {"max_days": 180},
    # Proxy mode's record of what the real cloud tried (events/models.py::
    # BlockedAttempt). Longer than events and with no size cap because the
    # table is tiny by construction — only blocked attempts land in it, never
    # the routine address rewrites — and "did the cloud ever try this?" is a
    # question asked long after the fact.
    "blocked": {"max_days": 365},
    # The device's own uploaded debug logs (http/handlers/upload_log.py), which
    # live in data_dir and not in the media tree.
    #
    # Sized from the real rate, not a guess: once a token is always available a
    # T5 uploads on EVERY poll, so every ~5 minutes, at 12-234 KB a time —
    # measured at ~36 MB/day on hardware. (PetKit's own capture showed 5 uploads
    # in 428 polls, which badly understates it; that device was mostly being
    # refused a token.) So the size cap is what actually bites and the age cap
    # is a backstop for a device that goes quiet: 18 MB is roughly half a day of
    # history at that rate, and nothing survives a day regardless. Raise it in
    # the panel if you are chasing something that needs a longer window.
    "deviceLog": {"max_mb": 18, "max_days": 1},
    # Staging for uploads whose `dev_upload_file_info_v2` never arrived — the
    # device rebooted mid-visit, a capability was toggled off between the PUT
    # and the report, a batch was lost. `media/pipeline.py` only deletes a raw
    # file when its metadata turns up AND the pipeline succeeds, so without this
    # the directory grew forever (and every miss made `_locate_raw_file`'s
    # fallback walk slower). Age-dominant on purpose: see RAW_MIN_AGE_SEC.
    "rawUpload": {"max_mb": 2048, "max_days": 2},
    # Pure cache: one JPEG per video, keyed by a hash of its path, so when
    # retention deletes the clip the thumbnail was orphaned. Evicting one costs
    # a single re-render, which is why this can be swept freely.
    "thumbnail": {"max_mb": 256, "max_days": 30},
}

#: The `.raw` staging dir is ALSO the stitcher's work_dir, and it holds uploads
#: legitimately waiting for their metadata. Nothing in flight is ever this old,
#: so the floor makes a size-driven eviction unable to delete a file a running
#: job is still writing. Enforced in code, not as a default a panel edit could
#: lower to zero.
RAW_MIN_AGE_SEC = 3600.0

#: Retention categories that sweep a DIRECTORY rather than a media table — the
#: file is the record. `(category, min_age)`.
RAW_CATEGORY = "rawUpload"
THUMBNAIL_CATEGORY = "thumbnail"

#: Key for the device-log caps above. Named once because it is both a retention
#: category and the thing `sweep_device_logs` sweeps, which is not a media table.
DEVICE_LOG_CATEGORY = "deviceLog"

MEDIA_CATEGORIES = ("fullVideo", "eventImage", "highLight", "dynamicVideo",
                    "cloudDouble", "wasteCheck", "healthPic")


@dataclass
class RetentionConfig:
    """The per-category size/age caps, as edited from the panel and persisted.

    `data` is `{category: {"max_mb": float | None, "max_days": float | None}}`
    over exactly the keys of `DEFAULT_RETENTION` — `update()` refuses to add
    others, so a category the sweeper asks about always exists. `None` (and a
    missing key) means "no cap"; a value is never negative, which would read
    to `max_bytes` as "delete everything".
    """

    data: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_RETENTION.items()})

    @classmethod
    def load(cls, data_dir: str) -> RetentionConfig:
        """Read the persisted overrides, falling back to the defaults.

        A missing file and a damaged one are deliberately treated the same:
        both mean "we have no usable overrides", and the sweeper deleting
        media must never be blocked from starting by a bad config file. Only
        the damaged case is logged (by `read_json`).
        """
        cfg = cls()
        path = Path(data_dir) / RETENTION_FILENAME
        stored = read_json(path, default={})
        if not isinstance(stored, dict):
            log.warning("Ignoring %s: expected an object, got %s", path, type(stored).__name__)
            return cfg
        for key, val in stored.items():
            if key in cfg.data and isinstance(val, dict):
                cfg.data[key].update(val)
        return cfg

    def save(self, data_dir: str) -> None:
        """Persist the caps. Atomic — a torn write would be read back as a
        damaged file and silently reset every cap to its default."""
        atomic_write_json(Path(data_dir) / RETENTION_FILENAME, self.data)

    def update(self, patch: dict | None) -> None:
        """Merge a partial {category: {max_mb|max_days: n}} patch, ignoring
        unknown categories/fields so a stray key from a bad request can't
        pollute the config.

        The patch is the raw JSON body of `POST /api/retention` (see
        `web/api/settings.py::api_retention`, which does not validate it), so every
        value goes through `to_float`:

        * bare `float()` raises on `"abc"` or a list — a 500 from the endpoint,
          with the fields earlier in the same request already applied, since
          this loop mutates in place;
        * `float()` also *accepts* `"inf"`/`"nan"`, which is worse than raising
          here: the bad value is persisted to retention.json and then detonates
          later inside the sweeper, at `int(mb * 1024 * 1024)`.

        A value that is not a positive finite number is treated as "no cap"
        (None), which is what 0 already meant — never as "prune everything",
        which is how a negative cap would otherwise read to `max_bytes`.
        """
        for key, val in (patch or {}).items():
            if key not in self.data or not isinstance(val, dict):
                continue
            for field_name in ("max_mb", "max_days"):
                if field_name in val:
                    number = to_float(val[field_name], None)
                    self.data[key][field_name] = number if number and number > 0 else None

    def max_bytes(self, category: str) -> int | None:
        """This category's size cap in bytes, or None for "no cap"."""
        mb = self.data.get(category, {}).get("max_mb")
        return int(mb * 1024 * 1024) if mb else None

    def max_age_sec(self, category: str) -> int | None:
        """This category's age cap in seconds, or None for "no cap"."""
        days = self.data.get(category, {}).get("max_days")
        return int(days * 86400) if days else None


def _unlink_all(paths: list[str]) -> None:
    """Unlink each path, logging but not raising (blocking — call via a thread).

    Takes the whole batch because a sweep can expire hundreds of files at
    once and one thread hop per unlink would cost more than the unlinks.
    """
    for path in paths:
        try:
            os.remove(path)
        except OSError as e:
            log.warning("Could not delete media file %s: %s", path, e)


async def _delete_media_files(store: EventStore, rows: list[dict]) -> None:
    """Unlink the expired files, then drop their rows.

    Files go first and in one batch: they are the only blocking work here, so
    they run off the loop while the store deletes — which aiosqlite already
    runs on its own thread — stay on it. A crash between the two steps leaves
    rows pointing at missing files, which the next sweep re-selects and
    finishes; the reverse order would leak files nothing references.

    A row is dropped even when its file could not be unlinked (already gone,
    or on a read-only mount), because keeping it would make the sweeper
    reconsider the same row on every pass forever.
    """
    if not rows:
        return
    await asyncio.to_thread(_unlink_all, [r["media_path"] for r in rows if r.get("media_path")])
    for row in rows:
        await store.delete_media(row["id"])


async def sweep_category(store: EventStore, category: str, config: RetentionConfig,
                         now: float | None = None) -> dict:
    """Enforce one capability's size + age caps. `media_for_retention`
    returns ready media newest-first; walked in reverse (oldest-first) so
    size trimming drops the oldest files, and age trimming is folded into
    the same single pass.

    Returns `{deleted, freed_bytes}` for this category. `now` is injectable so
    the age cap can be tested without sleeping."""
    now = now if now is not None else time.time()
    media = await store.media_for_retention(category)
    expired, freed = _select_expired(
        [(m.get("size_bytes") or 0, m.get("created_at")) for m in media],
        media, config.max_bytes(category), config.max_age_sec(category), now)

    await _delete_media_files(store, expired)
    return {"deleted": len(expired), "freed_bytes": freed}


def _select_expired(sizes_and_times: list[tuple[int, float | None]], items: list,
                    max_bytes: int | None, max_age: float | None,
                    now: float) -> tuple[list, int]:
    """Pick which of `items` must go, newest-first input, oldest dropped first.

    Pure, and shared by the media sweep and the device-log sweep so the two
    cannot drift: the rule that a single pass folds the size cap and the age cap
    together lives here once. `sizes_and_times` is positionally aligned with
    `items`, which lets the caller keep whatever row or path shape it has.

    Returns `(expired_items, freed_bytes)`.
    """
    total = sum(size for size, _ in sizes_and_times)
    expired: list = []
    freed = 0
    # Equal length by construction — both come from the same scan — so a
    # mismatch is a bug worth hearing about rather than silently truncating.
    for (size, created), item in zip(reversed(sizes_and_times), reversed(items),
                                     strict=True):
        over_size = max_bytes is not None and total > max_bytes
        over_age = (max_age is not None and created is not None
                    and (now - created) > max_age)
        if not (over_size or over_age):
            continue
        expired.append(item)
        total -= size
        freed += size
    return expired, freed


def _sweep_directory_sync(root: str, max_bytes: int | None, max_age: float | None,
                          now: float, min_age: float = 0.0, label: str = "") -> dict:
    """Scan, stat and unlink the oldest files in a directory tree.

    Blocking; call in a thread. The whole sweep is one thread hop rather than
    one per file: `sweep_all` itself runs on the event loop, for the same reason
    `_delete_media_files` batches its unlinks.

    Used for every store where the FILE is the record and there are no rows to
    reconcile — uploaded device logs, orphaned staging uploads, the thumbnail
    cache. A failed unlink is logged and the file reconsidered next pass.
    Nothing here raises: the sweeper is the only thing standing between a chatty
    device and a full disk, so it has to survive a tree that is missing,
    unreadable or being written to while it walks.

    Args:
        min_age: Files younger than this are NEVER deleted, whatever the size
            cap says. This is what makes the staging sweep safe: `.raw` holds
            both uploads still waiting for their metadata AND the stitcher's
            work files, so a purely size-driven eviction could delete a file
            that a running job is still writing. Age alone cannot express that —
            a size cap will happily evict the newest file in a tree if the tree
            is over budget — so the floor is enforced here rather than left to a
            generous default that a panel edit could undo.
    """
    entries: list[tuple[int, float | None, str]] = []
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue  # vanished mid-walk, or unreadable
                entries.append((st.st_size, st.st_mtime, path))
    except OSError as e:
        log.warning("Could not scan %s in %s: %s", label or "files", root, e)
        return {"deleted": 0, "freed_bytes": 0}

    entries.sort(key=lambda e: e[1] or 0, reverse=True)  # newest first
    expired, _freed = _select_expired([(size, ts) for size, ts, _ in entries],
                                      list(range(len(entries))),
                                      max_bytes, max_age, now)
    # Indices rather than paths above, so the age floor can be applied here with
    # the mtime still to hand — and so `freed_bytes` counts what was actually
    # unlinked, not what the cap wanted gone.
    keep_after = now - min_age if min_age else None
    doomed, freed = [], 0
    for i in expired:
        size, mtime, path = entries[i]
        if keep_after is not None and (mtime or 0) > keep_after:
            continue  # too young to be anything but in-flight
        doomed.append(path)
        freed += size
    _unlink_all(doomed)
    return {"deleted": len(doomed), "freed_bytes": freed}


async def sweep_device_logs(log_root: str, config: RetentionConfig,
                            now: float | None = None) -> dict:
    """Enforce the device-log size + age caps over `log_root`.

    A directory sweep rather than a table sweep: an uploaded log has no database
    row, because the file already carries everything a row would (the device id
    is the directory our own `pathPrefix` named, the time is the mtime, the size
    is the size). `web/panel.py` browses it the same way it browses captures.
    """
    return await sweep_directory(log_root, DEVICE_LOG_CATEGORY, config, now)


async def sweep_directory(root: str, category: str, config: RetentionConfig,
                          now: float | None = None, min_age: float = 0.0) -> dict:
    """Enforce one category's size + age caps over a directory tree."""
    if not root or not os.path.isdir(root):
        return {"deleted": 0, "freed_bytes": 0}
    now = now if now is not None else time.time()
    return await asyncio.to_thread(
        _sweep_directory_sync, root, config.max_bytes(category),
        config.max_age_sec(category), now, min_age, category)


async def sweep_all(store: EventStore, config: RetentionConfig,
                    now: float | None = None, log_root: str = "",
                    raw_root: str = "", thumb_root: str = "") -> dict:
    """Run every category's sweep plus the event age cap.

    Returns one entry per `MEDIA_CATEGORIES` key, each `{deleted,
    freed_bytes}`, plus `{"events": {"removed": n}}` and `{"blocked":
    {"removed": n}}` — the caller only logs it when something was actually
    removed.

    Runs on the event loop: every store call is a coroutine, and the sweep's
    only blocking work — the unlinks — is handed to a thread in one batch per
    category (see `_delete_media_files`).
    """
    now = now if now is not None else time.time()
    summary = {cat: await sweep_category(store, cat, config, now) for cat in MEDIA_CATEGORIES}
    events_max_age = config.max_age_sec("events")
    removed_events = await store.prune_events(now - events_max_age) if events_max_age else 0
    summary["events"] = {"removed": removed_events}

    blocked_max_age = config.max_age_sec("blocked")
    removed_blocked = (await store.prune_blocked_attempts(now - blocked_max_age)
                       if blocked_max_age else 0)
    summary["blocked"] = {"removed": removed_blocked}

    # Defaulted, so the two existing two-argument call sites keep working; an
    # empty root simply means device-log collection was never wired up.
    summary[DEVICE_LOG_CATEGORY] = await sweep_device_logs(log_root, config, now)

    # Directory sweeps for the two stores nothing else prunes. Both default to
    # an empty root so the existing two-argument call sites keep working.
    summary[RAW_CATEGORY] = await sweep_directory(
        raw_root, RAW_CATEGORY, config, now, min_age=RAW_MIN_AGE_SEC)
    summary[THUMBNAIL_CATEGORY] = await sweep_directory(
        thumb_root, THUMBNAIL_CATEGORY, config, now)
    return summary


class RetentionSweeper:
    """Runs `sweep_all` forever on a timer.

    Holds the live `RetentionConfig` object rather than a copy of its numbers,
    so a cap edited from the panel takes effect on the next pass without any
    restart or re-wiring.
    """

    def __init__(self, store: EventStore, config: RetentionConfig,
                 interval: float = 600.0, log_root: str = "",
                 raw_root: str = "", thumb_root: str = "") -> None:
        """`interval` is in seconds, and is only the gap BETWEEN passes.

        A sweep's own duration is not subtracted from it (see `run`), so this
        is a lower bound on the period, not a schedule.

        `log_root`, `raw_root` and `thumb_root` are the three directory
        stores — uploaded device logs, staged uploads awaiting their metadata,
        and the thumbnail cache. Each empties to "skip that sweep",
        which is what a build with no device-log collection wired up gets.
        """
        self._store = store
        self._config = config
        self._interval = interval
        self._log_root = log_root
        self._raw_root = raw_root
        self._thumb_root = thumb_root

    async def run(self) -> None:
        """Sweep every `interval` seconds until cancelled.

        A failing sweep is logged and the loop continues: the sweeper is the
        only thing standing between the add-on and a full disk, so it must
        survive one bad row or a transient store error.
        """
        while True:
            try:
                # Awaited, not `asyncio.to_thread`: the store is async, so its
                # coroutines can only be driven by this loop. What must not
                # stall the loop — the unlinks — goes to a thread inside the
                # sweep instead, batched per category.
                summary = await sweep_all(
                    self._store, self._config, log_root=self._log_root,
                    raw_root=self._raw_root, thumb_root=self._thumb_root)
                if any(v.get("deleted") or v.get("removed") for v in summary.values()):
                    log.info("Retention sweep: %s", summary)
            except Exception:
                log.exception("Retention sweep failed")
            await asyncio.sleep(self._interval)
