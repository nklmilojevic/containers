import json
import os
import tempfile
from pathlib import Path

from petkit_local.utils.jsonio import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    read_json,
)


def _entries(directory) -> list[str]:
    return sorted(os.listdir(directory))


def test_atomic_write_json_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        data = {"10000001": {"device_type": "t5", "secret": "abc"}}
        atomic_write_json(path, data)

        assert json.loads(path.read_text()) == data
        assert read_json(path, None) == data


def test_atomic_write_json_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "deeper" / "devices.json"
        atomic_write_json(path, {"a": 1})

        assert path.exists()
        assert json.loads(path.read_text()) == {"a": 1}


def test_atomic_write_json_leaves_no_temp_files():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        atomic_write_json(path, {"a": 1})
        atomic_write_json(path, {"a": 2})

        assert _entries(tmp) == ["devices.json"]


def test_atomic_write_json_overwrites_existing_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        path.write_text(json.dumps({"old": True}))

        atomic_write_json(path, {"new": True})

        assert json.loads(path.read_text()) == {"new": True}


def test_atomic_write_json_keeps_target_intact_when_serialization_fails():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        original = {"10000001": {"device_type": "t5", "secret": "keep-me"}}
        atomic_write_json(path, original)

        # A dict that partially serialises: json emits "good" before it hits the
        # unsupported object and gives up.
        broken = {"good": 1, "bad": object()}
        raised = False
        try:
            atomic_write_json(path, broken)
        except TypeError:
            raised = True

        assert raised, "TypeError must propagate to the caller"
        assert json.loads(path.read_text()) == original
        assert _entries(tmp) == ["devices.json"], "temp file must be cleaned up"


def test_atomic_write_json_accepts_str_path():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "devices.json")
        atomic_write_json(path, [1, 2, 3])

        assert read_json(path, None) == [1, 2, 3]


def test_atomic_write_json_indent_none_is_compact():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "compact.json"
        atomic_write_json(path, {"a": 1, "b": 2}, indent=None)

        assert "\n" not in path.read_text()


def test_atomic_write_text_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sub" / "bucket_aes_key.txt"
        atomic_write_text(path, "0123456789abcdef")

        assert path.read_text() == "0123456789abcdef"
        assert _entries(path.parent) == ["bucket_aes_key.txt"]


def test_atomic_write_bytes_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "blob.bin"
        atomic_write_bytes(path, b"\xff\xd8\xff\xe0")

        assert path.read_bytes() == b"\xff\xd8\xff\xe0"


def test_atomic_write_bytes_leaves_original_when_dir_is_unwritable():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return  # root ignores the mode bits, so there is nothing to observe
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        atomic_write_json(path, {"keep": True})
        os.chmod(tmp, 0o500)
        try:
            raised = False
            try:
                atomic_write_json(path, {"lost": True})
            except OSError:
                raised = True
            assert raised, "OSError must propagate to the caller"
            assert json.loads(path.read_text()) == {"keep": True}
        finally:
            os.chmod(tmp, 0o700)


def test_read_json_missing_file_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        assert read_json(Path(tmp) / "nope.json", {}) == {}
        assert read_json(Path(tmp) / "nope.json", None) is None


def test_read_json_truncated_file_returns_default_instead_of_raising():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        # Exactly what a kill mid-write used to leave behind.
        path.write_text('{"10000001": {"device_ty')

        assert read_json(path, {}) == {}


def test_read_json_empty_file_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "devices.json"
        path.write_text("")

        assert read_json(path, {"fallback": True}) == {"fallback": True}


def test_read_json_directory_path_returns_default():
    with tempfile.TemporaryDirectory() as tmp:
        assert read_json(tmp, {"fallback": True}) == {"fallback": True}
