from petkit_local.utils.timeutil import local_day_bounds


async def test_stats_empty_for_pet_with_no_visits(event_store):
    store = event_store
    stats = await store.pet_visit_stats(1, now=1000.0)
    assert stats == {
        "last_visit_ts": None, "visits_today": 0,
        "last_visit_weight": None, "last_visit_duration": None,
        "last_device_id": None,
    }


async def test_stats_pick_latest_visit_and_weight(event_store):
    store = event_store
    await store.upsert_event({"device_id": 5, "event_type": "pet_out", "event_kind": "toilet_visit",
                              "pet_id": 1, "ts": 100.0, "content_json": '{"pet_weight": 2200}'})
    await store.upsert_event({"device_id": 5, "event_type": "pet_out", "event_kind": "toilet_visit",
                              "pet_id": 1, "ts": 200.0, "content_json": '{"pet_weight": 2300}'})
    stats = await store.pet_visit_stats(1, now=300.0)
    assert stats["last_visit_ts"] == 200.0
    assert stats["last_visit_weight"] == 2300.0
    assert stats["last_device_id"] == 5


async def test_stats_computes_duration_from_paired_pet_in(event_store):
    store = event_store
    await store.upsert_event({"device_id": 5, "event_type": "pet_in", "event_kind": "toilet_visit",
                              "related_event": "r1", "ts": 100.0})
    await store.upsert_event({"device_id": 5, "event_type": "pet_out", "event_kind": "toilet_visit",
                              "pet_id": 1, "related_event": "r1", "ts": 156.0})
    stats = await store.pet_visit_stats(1, now=200.0)
    assert stats["last_visit_duration"] == 56.0


async def test_a_visit_is_reported_in_whole_seconds_and_whole_grams(event_store):
    """Both stamps are report ARRIVAL times, so the sub-second part of their
    difference is transport latency rather than time the cat spent in the box —
    `codes.py` measures a 3.8s median for `ts - start_time` on this kind of
    pair. Subtracting them raw published "57.317591428756714 s" to Home
    Assistant: fifteen digits of precision the inputs cannot support.

    The weight beside it had the same shape in miniature. All 58 `pet_weight`
    values in the captures are integers, so `float()` was inventing the `.0`.
    """
    store = event_store
    await store.upsert_event({"device_id": 5, "event_type": "pet_in", "event_kind": "toilet_visit",
                              "related_event": "r1", "ts": 1000.0})
    await store.upsert_event({"device_id": 5, "event_type": "pet_out", "event_kind": "toilet_visit",
                              "pet_id": 1, "related_event": "r1", "ts": 1057.317591428756714,
                              "content_json": '{"pet_weight": 2223}'})
    stats = await store.pet_visit_stats(1, now=2000.0)

    assert stats["last_visit_duration"] == 57
    assert isinstance(stats["last_visit_duration"], int)
    assert stats["last_visit_weight"] == 2223
    assert isinstance(stats["last_visit_weight"], int)


async def test_stats_visits_today_counts_only_same_local_day(event_store):
    """The boundary is LOCAL midnight, not UTC.

    Timestamps are derived from `local_day_bounds` rather than written as
    multiples of 86400, so this passes in any developer's timezone. Hardcoding
    them assumed a UTC boundary, and on a UTC+2 machine the "previous day"
    event landed inside the same local day and the count came out at 3.
    """
    store = event_store
    now = 86400.0 * 100 + 43200  # midday of an arbitrary day
    day_start, _, _ = local_day_bounds(now=now)
    for ts in (day_start + 10, day_start + 20, day_start - 100):
        await store.upsert_event({"device_id": 5, "event_type": "pet_out",
                                  "event_kind": "toilet_visit", "pet_id": 1, "ts": ts})
    stats = await store.pet_visit_stats(1, now=now)
    assert stats["visits_today"] == 2


async def test_stats_ignore_other_pets_and_non_visit_events(event_store):
    store = event_store
    await store.upsert_event({"device_id": 5, "event_type": "pet_out", "event_kind": "toilet_visit",
                              "pet_id": 2, "ts": 100.0})  # different pet
    await store.upsert_event({"device_id": 5, "event_type": "clean_over", "event_kind": "cleaning",
                              "pet_id": 1, "ts": 150.0})  # not a toilet_visit
    stats = await store.pet_visit_stats(1, now=200.0)
    assert stats["last_visit_ts"] is None
    assert stats["visits_today"] == 0
