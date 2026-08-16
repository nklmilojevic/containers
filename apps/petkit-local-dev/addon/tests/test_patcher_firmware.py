"""Patcher integration tests against real device firmware.

These tests exercise `patch_ctrl`, `patch_cloud` and `patch_ca_bundle` on actual
binaries extracted from firmware images. They are NOT part of the default suite:

    pytest                    # skips these
    pytest --firmware         # runs them (needs tests/firmware/ populated)

The firmware directory is gitignored. Layout:

    tests/firmware/
    ├── t5/943/app_261463.bin
    ├── t6/daniel/app.bin
    ├── d4sh/867/app_261301.bin
    └── w7h/456/app_262863.bin

Each .bin is a uImage (64-byte header) wrapping a squashfs filesystem. The tests
extract binaries from it once per session via `dd | unsquashfs`.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

FIRMWARE_DIR = Path(__file__).parent / "firmware"
UIMAGE_HEADER_SIZE = 64

_extract_cache: dict[str, Path] = {}


def _discover_firmware() -> list[tuple[str, str, Path]]:
    """Walk tests/firmware/ and return (device, version, bin_path) triples."""
    if not FIRMWARE_DIR.is_dir():
        return []
    results = []
    for device_dir in sorted(FIRMWARE_DIR.iterdir()):
        if not device_dir.is_dir():
            continue
        device = device_dir.name
        for version_dir in sorted(device_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            version = version_dir.name
            for bin_file in sorted(version_dir.glob("*.bin")):
                results.append((device, version, bin_file))
    return results


ALL_FIRMWARE = _discover_firmware()

if not ALL_FIRMWARE:
    pytest.skip("tests/firmware/ not found or empty", allow_module_level=True)


# Classified at test time by reading the extracted ctrl ELF header, not by a
# static table. At COLLECTION time we use the directory name as a proxy —
# the firmware is compressed squashfs so the ELF cannot be read without
# extraction, and extraction requires unsquashfs which may be absent.
# The first test (`test_all_firmware_is_uimage_squashfs`) extracts everything,
# so by the time an arch-specific test runs, `_read_binary` works.
_ARM_DEVICE_DIRS = {"w7h"}
MIPS_FIRMWARE = [(d, v, p) for d, v, p in ALL_FIRMWARE if d not in _ARM_DEVICE_DIRS]
ARM_FIRMWARE = [(d, v, p) for d, v, p in ALL_FIRMWARE if d in _ARM_DEVICE_DIRS]


def _fw_id(val):
    if isinstance(val, tuple) and len(val) == 3:
        return f"{val[0]}-{val[1]}"
    return None


def _extract_squashfs(bin_path: Path) -> Path:
    """Extract squashfs from a uImage .bin, caching per session."""
    key = str(bin_path)
    if key in _extract_cache:
        return _extract_cache[key]

    if not shutil.which("unsquashfs"):
        pytest.skip("unsquashfs not installed (brew install squashfs)")

    cache_dir = Path(tempfile.mkdtemp(prefix="petkit_fw_"))
    sqfs = cache_dir / "app.sqfs"

    with open(bin_path, "rb") as f:
        f.seek(UIMAGE_HEADER_SIZE)
        sqfs.write_bytes(f.read())

    result = subprocess.run(
        ["unsquashfs", "-d", str(cache_dir / "root"), str(sqfs)],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"unsquashfs failed for {bin_path.name}: {result.stderr.decode()}")

    _extract_cache[key] = cache_dir / "root"
    return cache_dir / "root"


def _read_binary(bin_path: Path, name: str) -> bytes:
    """Extract and read a single binary from a firmware image."""
    root = _extract_squashfs(bin_path)
    target = root / "bin" / name
    if not target.exists():
        pytest.skip(f"{name} not found in {bin_path.name}")
    return target.read_bytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(params=MIPS_FIRMWARE, ids=[f"{d}-{v}" for d, v, _ in MIPS_FIRMWARE])
def mips_fw(request):
    return request.param


@pytest.fixture(params=ARM_FIRMWARE, ids=[f"{d}-{v}" for d, v, _ in ARM_FIRMWARE])
def arm_fw(request):
    return request.param


@pytest.fixture(params=ALL_FIRMWARE, ids=[f"{d}-{v}" for d, v, _ in ALL_FIRMWARE])
def any_fw(request):
    return request.param


# ---------------------------------------------------------------------------
# Extraction sanity
# ---------------------------------------------------------------------------

@pytest.mark.firmware
def test_all_firmware_is_uimage_squashfs(any_fw):
    device, version, bin_path = any_fw
    data = bin_path.read_bytes()
    assert data[:4] == b'\x27\x05\x19\x56', "not a uImage"
    root = _extract_squashfs(bin_path)
    assert (root / "bin" / "ctrl").exists()


# ---------------------------------------------------------------------------
# MQTT patcher
# ---------------------------------------------------------------------------

@pytest.mark.firmware
def test_mqtt_patch_ctrl(mips_fw):
    from petkit_local.patchers.mqtt import patch_ctrl
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "ctrl")
    patched, offset = patch_ctrl(data)
    assert len(patched) == len(data)
    assert offset > 0


@pytest.mark.firmware
def test_mqtt_patch_already_patched(mips_fw):
    from petkit_local.patchers.mqtt import patch_ctrl
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "ctrl")
    patched, _ = patch_ctrl(data)
    with pytest.raises(ValueError, match="already patched"):
        patch_ctrl(patched)


@pytest.mark.firmware
def test_mips_ctrl_has_verify_symbol(mips_fw):
    from elftools.elf.elffile import ELFFile
    from petkit_local.patchers.mqtt import SYMBOL_NAME
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "ctrl")
    elf = ELFFile(io.BytesIO(data))
    dynsym = elf.get_section_by_name(".dynsym")
    assert dynsym is not None, f"{device}-{version} ctrl has no .dynsym"
    names = [s.name for s in dynsym.iter_symbols()
             if s["st_info"]["type"] == "STT_FUNC"]
    assert SYMBOL_NAME in names


@pytest.mark.firmware
def test_mips_ctrl_symbol_offset_in_bounds(mips_fw):
    from petkit_local.patchers.mqtt import find_offset
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "ctrl")
    offset = find_offset(data)
    assert 0 < offset < len(data) - 16


@pytest.mark.firmware
def test_mqtt_patch_ctrl_arm(arm_fw):
    from petkit_local.patchers.mqtt import patch_ctrl
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "ctrl")
    patched, offset = patch_ctrl(data)
    assert len(patched) == len(data)
    assert offset > 0


@pytest.mark.firmware
def test_mqtt_patch_already_patched_arm(arm_fw):
    from petkit_local.patchers.mqtt import patch_ctrl
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "ctrl")
    patched, _ = patch_ctrl(data)
    with pytest.raises(ValueError, match="already patched"):
        patch_ctrl(patched)


@pytest.mark.firmware
def test_mqtt_arm_prologue_is_unique(arm_fw):
    from petkit_local.patchers.mqtt import _ARM_PROLOGUE
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "ctrl")
    assert data.count(_ARM_PROLOGUE) == 1


# ---------------------------------------------------------------------------
# Cloud patcher
# ---------------------------------------------------------------------------

@pytest.mark.firmware
def test_cloud_patch(mips_fw):
    from petkit_local.patchers.cloud import patch_cloud
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "cloud")
    patched, applied = patch_cloud(data)
    assert len(patched) == len(data)
    newly = [a for a in applied if a["status"] == "applied"]
    assert len(newly) == 2, f"expected 2 patches, got {[a['name'] for a in newly]}"


@pytest.mark.firmware
def test_cloud_patch_already_patched(mips_fw):
    from petkit_local.patchers.cloud import patch_cloud
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "cloud")
    patched, _ = patch_cloud(data)
    with pytest.raises(ValueError, match="already patched"):
        patch_cloud(patched)


@pytest.mark.firmware
def test_cloud_isCClassIP_xori_within_function(mips_fw):
    """The xori instruction is inside the symbol range, not necessarily the last word."""
    from elftools.elf.elffile import ELFFile
    from petkit_local.patchers.cloud import ISCC_ORIGINAL
    from petkit_local.patchers.verify import MIPS_ELF_BASE
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "cloud")
    elf = ELFFile(io.BytesIO(data))
    dynsym = elf.get_section_by_name(".dynsym")
    assert dynsym is not None

    for sym in dynsym.iter_symbols():
        if sym.name == "isCClassIP" and sym["st_info"]["type"] == "STT_FUNC":
            start = sym["st_value"] - MIPS_ELF_BASE
            size = sym["st_size"]
            func_data = data[start:start + size]
            assert ISCC_ORIGINAL in func_data, (
                f"xori s0,s0,1 not found in isCClassIP on {device}-{version}"
            )
            return
    pytest.fail(f"isCClassIP symbol not found on {device}-{version}")


@pytest.mark.firmware
def test_cloud_connect_to_within_function(mips_fw):
    from elftools.elf.elffile import ELFFile
    from petkit_local.patchers.cloud import CONNECT_TO_PATTERN
    from petkit_local.patchers.verify import MIPS_ELF_BASE
    device, version, bin_path = mips_fw
    data = _read_binary(bin_path, "cloud")
    elf = ELFFile(io.BytesIO(data))
    dynsym = elf.get_section_by_name(".dynsym")
    assert dynsym is not None

    for sym in dynsym.iter_symbols():
        if sym.name == "cloud_do_queue_upload" and sym["st_info"]["type"] == "STT_FUNC":
            start = sym["st_value"] - MIPS_ELF_BASE
            size = sym["st_size"]
            func_data = data[start:start + size]
            assert CONNECT_TO_PATTERN in func_data, (
                f"CONNECT_TO pattern not found in cloud_do_queue_upload on {device}-{version}"
            )
            return
    pytest.fail(f"cloud_do_queue_upload symbol not found on {device}-{version}")


@pytest.mark.firmware
def test_cloud_patch_arm(arm_fw):
    from petkit_local.patchers.cloud import patch_cloud
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "cloud")
    patched, applied = patch_cloud(data)
    assert len(patched) == len(data)
    newly = [a for a in applied if a["status"] == "applied"]
    assert len(newly) == 6, f"expected 6 patches (1 isCC + 5 CT), got {[a['name'] for a in newly]}"


@pytest.mark.firmware
def test_cloud_patch_already_patched_arm(arm_fw):
    from petkit_local.patchers.cloud import patch_cloud
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "cloud")
    patched, _ = patch_cloud(data)
    with pytest.raises(ValueError, match="already patched"):
        patch_cloud(patched)


@pytest.mark.firmware
def test_cloud_arm_isCClassIP_string_present(arm_fw):
    device, version, bin_path = arm_fw
    data = _read_binary(bin_path, "cloud")
    assert b"isCClassIP" in data


# ---------------------------------------------------------------------------
# CA patcher
# ---------------------------------------------------------------------------

@pytest.mark.firmware
def test_ca_bundle_valid(any_fw):
    from petkit_local.patchers.verify import assert_ca_bundle
    device, version, bin_path = any_fw
    data = _read_binary(bin_path, "ca.crt")
    assert_ca_bundle(data, "ca.crt")


@pytest.mark.firmware
def test_ca_patch_appends(any_fw):
    from petkit_local.patchers.cacert import patch_ca_bundle
    device, version, bin_path = any_fw
    original = _read_binary(bin_path, "ca.crt")
    ours = b"-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n"
    patched = patch_ca_bundle(original, ours)
    orig_count = original.count(b"-----BEGIN CERTIFICATE-----")
    assert patched.count(b"-----BEGIN CERTIFICATE-----") == orig_count + 1
