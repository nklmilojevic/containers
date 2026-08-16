import os
import tempfile
from pathlib import Path

import pytest

from petkit_local.devices.base import Device
from petkit_local.events.store import EventStore
from petkit_local.media import crypto, pipeline

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"A" * 60  # 64 bytes, block-aligned


#: Every store `_env` handed out this test, drained by the autouse fixture
#: below. A store holds an aiosqlite pool bound to the test's own event loop.
_OPEN_STORES: list[EventStore] = []


@pytest.fixture(autouse=True)
async def _close_stores():
    yield
    while _OPEN_STORES:
        await _OPEN_STORES.pop().close()


def _env():
    """A temp raw/media tree, a store inside it, and a device to attribute to.

    The `TemporaryDirectory` is returned so the caller can keep it alive: the
    tree is deleted when it is collected, and these tests read files back.
    """
    tmp = tempfile.TemporaryDirectory()
    raw_root = os.path.join(tmp.name, "raw")
    media_root = os.path.join(tmp.name, "media")
    os.makedirs(raw_root, exist_ok=True)
    os.makedirs(media_root, exist_ok=True)
    config = {"media_raw_root": raw_root, "media_root": media_root, "data_dir": tmp.name}
    store = EventStore(Path(tmp.name) / "petkit.db")
    _OPEN_STORES.append(store)
    device = Device(device_type="t5", petkit_id=1, serial_number="SN")
    return tmp, config, store, device, raw_root, media_root


def test_locate_raw_file_by_file_url_path():
    tmp, config, store, device, raw_root, media_root = _env()
    rel = "t5/1/eventImage/abc123.bin"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(JPEG_BYTES)

    info = {"fileId": "abc123", "fileUrl": f"https://localhost:9000/{rel}"}
    found = pipeline._locate_raw_file(raw_root, info)
    assert found == full


def test_locate_raw_file_falls_back_to_recursive_search():
    tmp, config, store, device, raw_root, media_root = _env()
    nested = os.path.join(raw_root, "t5", "1", "highLight")
    os.makedirs(nested, exist_ok=True)
    full = os.path.join(nested, "somekey-xyz789-ext")
    Path(full).write_bytes(JPEG_BYTES)

    found = pipeline._locate_raw_file(raw_root, {"fileId": "xyz789"})
    assert found == full


def test_locate_raw_file_returns_none_when_missing():
    tmp, config, store, device, raw_root, media_root = _env()
    assert pipeline._locate_raw_file(raw_root, {"fileId": "nope"}) is None


def test_locate_raw_file_rejects_path_traversal_in_file_url():
    # fileUrl is device-controlled (any client can POST dev_upload_file_info_v2);
    # a crafted ../ path must never resolve to a file outside raw_root, even if
    # one exists there — otherwise it could be read and then deleted by the
    # pipeline (see process_file_info's os.remove(src)).
    tmp, config, store, device, raw_root, media_root = _env()
    secret = os.path.join(tmp.name, "secret.txt")
    Path(secret).write_bytes(b"top secret")

    info = {"fileId": "whatever", "fileUrl": "https://localhost:9000/../secret.txt"}
    found = pipeline._locate_raw_file(raw_root, info)
    assert found is None  # no fileId-matching fallback file exists either


def test_locate_raw_file_traversal_falls_back_to_fileid_search_within_root():
    tmp, config, store, device, raw_root, media_root = _env()
    secret = os.path.join(tmp.name, "secret.txt")
    Path(secret).write_bytes(b"top secret")
    # A legitimately-placed raw file also named with the same fileId — the
    # traversal attempt in fileUrl must be ignored, not silently accepted,
    # and the safe fallback search must still find the real one.
    legit = os.path.join(raw_root, "t5", "1", "eventImage", "secrettxt-real")
    os.makedirs(os.path.dirname(legit), exist_ok=True)
    Path(legit).write_bytes(JPEG_BYTES)

    info = {"fileId": "secrettxt", "fileUrl": "https://localhost:9000/../secret.txt"}
    found = pipeline._locate_raw_file(raw_root, info)
    assert found == legit


async def test_process_file_info_plaintext_image_end_to_end():
    tmp, config, store, device, raw_root, media_root = _env()
    rel = "t5/1/eventImage/f1"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(JPEG_BYTES)

    info = {"fileId": "f1", "fileUrl": f"https://localhost:9000/{rel}",
            "cycleType": "eventImage", "fileType": "image", "encrypt": "0",
            "eventId": "session1"}

    result = await pipeline.process_file_info(device, info, config, store)
    assert result is not None
    assert result.endswith(".jpg")
    assert os.path.isfile(result)
    assert Path(result).read_bytes() == JPEG_BYTES
    assert not os.path.exists(full)  # raw file removed

    media = await store.get_media_by_file_id("f1")
    assert media["status"] == "ready"
    assert media["media_path"] == result


async def test_process_file_info_decrypts_when_encrypted():
    tmp, config, store, device, raw_root, media_root = _env()
    key = crypto.resolve_key(config)
    iv = b"\xcc" * 16

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(JPEG_BYTES) + encryptor.finalize()

    rel = "t5/1/eventImage/f2"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(ciphertext)

    info = {"fileId": "f2", "fileUrl": f"https://localhost:9000/{rel}",
            "cycleType": "eventImage", "fileType": "image", "encrypt": "1",
            "aesIv": "0x" + iv.hex()}

    result = await pipeline.process_file_info(device, info, config, store)
    assert Path(result).read_bytes() == JPEG_BYTES


async def test_process_file_info_missing_raw_marks_status():
    tmp, config, store, device, raw_root, media_root = _env()
    info = {"fileId": "ghost", "cycleType": "eventImage"}

    result = await pipeline.process_file_info(device, info, config, store)
    assert result is None
    assert (await store.get_media_by_file_id("ghost"))["status"] == "missing"


async def test_process_file_info_skips_disabled_capability():
    tmp, config, store, device, raw_root, media_root = _env()
    device.config["capabilities"] = {"eventImage": False}
    rel = "t5/1/eventImage/f3"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(JPEG_BYTES)

    info = {"fileId": "f3", "fileUrl": f"https://localhost:9000/{rel}", "cycleType": "eventImage"}

    result = await pipeline.process_file_info(device, info, config, store)
    assert result is None
    assert (await store.get_media_by_file_id("f3"))["status"] == "skipped"
    assert os.path.exists(full)  # untouched — never even read


async def test_process_file_info_video_falls_back_to_ts_without_ffmpeg():
    tmp, config, store, device, raw_root, media_root = _env()
    ts_bytes = bytes([0x47]) + b"\x00" * 187
    ts_bytes = ts_bytes * 3
    rel = "t5/1/fullVideo/f4"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(ts_bytes)

    info = {"fileId": "f4", "fileUrl": f"https://localhost:9000/{rel}",
            "cycleType": "fullVideo", "fileType": "video", "encrypt": "0"}

    from petkit_local.media import transcode
    orig = transcode.have_ffmpeg
    transcode.have_ffmpeg = lambda: False
    try:
        result = await pipeline.process_file_info(device, info, config, store)
    finally:
        transcode.have_ffmpeg = orig

    assert result is not None
    assert result.endswith(".ts")
    assert Path(result).read_bytes() == ts_bytes


async def test_waste_photo_index_numbers_sequentially():
    """The 'Check waste' burst (SHIT_PICTURE) gets a bare arrival index — no
    false '(n of m)' total, which was wrong for every photo but the last."""
    tmp, config, store, device, raw_root, media_root = _env()

    for i, fid in enumerate(("a", "b", "c"), start=1):
        rel = f"t5/1/waste/{fid}"
        full = os.path.join(raw_root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        Path(full).write_bytes(JPEG_BYTES)
        info = {"fileId": fid, "fileUrl": f"https://localhost:9000/{rel}",
                "moduleType": "SHIT_PICTURE", "fileType": "jpeg", "eventId": "same-visit"}
        result = await pipeline.process_file_info(device, info, config, store)
        assert result.endswith(f" {i}.jpg"), result
        assert "of" not in os.path.basename(result)
        assert "/Waste/" in result


async def test_health_photo_lands_in_health_folder():
    """HEALTH_PRED is the 6th moduleType (stool analysis) — it must not fall
    into an 'Other' folder."""
    tmp, config, store, device, raw_root, media_root = _env()
    rel = "t5/1/health/h1"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(JPEG_BYTES)
    info = {"fileId": "h1", "fileUrl": f"https://localhost:9000/{rel}",
            "moduleType": "HEALTH_PRED", "fileType": "jpeg", "eventId": "visit1"}
    result = await pipeline.process_file_info(device, info, config, store)
    assert "/Health/" in result
    assert (await store.get_media_by_file_id("h1"))["category"] == "healthPic"


async def test_waste_gallery_and_poster_land_in_different_folders():
    """SHIT_PICTURE (the 5-photo gallery) and EVENT_PREVIEW (one poster) share
    the eventImage capability but are different roles — they must not pile
    into the same folder, which is what hid the gallery."""
    tmp, config, store, device, raw_root, media_root = _env()

    out = {}
    for module_type, fid in (("SHIT_PICTURE", "w1"), ("EVENT_PREVIEW", "p1")):
        rel = f"t5/1/{fid}"
        full = os.path.join(raw_root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        Path(full).write_bytes(JPEG_BYTES)
        info = {"fileId": fid, "fileUrl": f"https://localhost:9000/{rel}",
                "moduleType": module_type, "fileType": "jpeg", "eventId": "ep1"}
        out[module_type] = await pipeline.process_file_info(device, info, config, store)

    assert "/Waste/" in out["SHIT_PICTURE"]
    assert "/Snapshots/" in out["EVENT_PREVIEW"]


async def test_shared_capability_role_is_gated_by_its_capability():
    """Disabling `eventImage` must also stop the waste gallery, which rides on
    that capability under a different category name."""
    tmp, config, store, device, raw_root, media_root = _env()
    device.config["capabilities"] = {"eventImage": False}

    rel = "t5/1/w"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(JPEG_BYTES)
    info = {"fileId": "w", "fileUrl": f"https://localhost:9000/{rel}",
            "moduleType": "SHIT_PICTURE", "fileType": "jpeg", "eventId": "ep1"}

    assert await pipeline.process_file_info(device, info, config, store) is None
    assert (await store.get_media_by_file_id("w"))["status"] == "skipped"


async def _run_substream(device, config, store, raw_root, file_id):
    ts_bytes = (bytes([0x47]) + b"\x00" * 187) * 3
    rel = f"t5/1/{file_id}"
    full = os.path.join(raw_root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    Path(full).write_bytes(ts_bytes)
    info = {"fileId": file_id, "fileUrl": f"https://localhost:9000/{rel}",
            "moduleType": "CLOUD_DOUBLE", "fileType": "video/x-mpg", "eventId": "ep1"}

    from petkit_local.media import transcode
    orig = transcode.have_ffmpeg
    transcode.have_ffmpeg = lambda: False
    try:
        return await pipeline.process_file_info(device, info, config, store)
    finally:
        transcode.have_ffmpeg = orig


async def test_substream_is_stored_in_its_own_folder_when_recordings_are_enabled():
    tmp, config, store, device, raw_root, media_root = _env()
    result = await _run_substream(device, config, store, raw_root, "sub_on")
    assert result is not None
    assert "/Timelapse/" in result


async def test_substream_follows_the_recordings_capability_it_rides_on():
    """cloudDouble isn't a capability name, but the device uploads it under
    the fullVideo slot — so turning Recordings off must stop it too, or the
    toggle would only half-work."""
    tmp, config, store, device, raw_root, media_root = _env()
    device.config["capabilities"] = {"fullVideo": False}
    assert await _run_substream(device, config, store, raw_root, "sub_off") is None
    assert (await store.get_media_by_file_id("sub_off"))["status"] == "skipped"
