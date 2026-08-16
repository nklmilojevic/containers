"""Format checks a downloaded device file must pass before we patch it.

A patcher fetches its target over a busybox httpd the device was told to start
(`patchers/common.py::download_from_device`) and writes the result back to
/system. Nothing between those two points ever asked whether the bytes were
what we think they are, so a truncated transfer, an httpd directory listing or
a device from an unexpected family all reached the byte-level patchers, which
would then search for a symbol, fail to find it, and raise something that reads
like a firmware mismatch rather than a transport problem.

These checks are deliberately pure and synchronous: they take bytes and raise.
That keeps them unit-testable with a hand-built 52-byte header instead of a
firmware blob committed to the repo, and it lets the byte-level patchers own
their own preconditions rather than trusting whoever called them.

The ELF reader here is hand-rolled rather than pyelftools, because
`patchers/mqtt.py` treats pyelftools as OPTIONAL (it falls back to a byte
pattern when the import fails) and a validator that made it mandatory would
turn a soft dependency into a hard one.
"""
from __future__ import annotations

#: Offsets into `Elf32_Ehdr`. Only the identification block and the two 16-bit
#: fields that follow it are needed, so the whole check is the first 20 bytes.
_EI_CLASS = 4        # 1 = 32-bit, 2 = 64-bit
_EI_DATA = 5         # 1 = little-endian, 2 = big-endian
_E_TYPE = 16         # 2 = ET_EXEC (fixed load address), 3 = ET_DYN (PIE)
_E_MACHINE = 18      # 8 = EM_MIPS

ELF_MAGIC = b"\x7fELF"
ELFCLASS32 = 1
ELFDATA2LSB = 1
ET_EXEC = 2
ET_DYN = 3
EM_MIPS = 8
EM_ARM = 40

MIPS_ELF_BASE = 0x400000

#: Enough bytes to read `e_machine`, which is the last field we look at.
_MIN_ELF_HEADER = 20

_E_TYPE_NAMES = {0: "ET_NONE", 1: "ET_REL", ET_EXEC: "ET_EXEC", ET_DYN: "ET_DYN (PIE)", 4: "ET_CORE"}
_E_MACHINE_NAMES = {3: "EM_386", EM_MIPS: "EM_MIPS", 40: "EM_ARM", 62: "EM_X86_64", 183: "EM_AARCH64"}


def _u16le(data: bytes, offset: int) -> int:
    """Read a 16-bit little-endian field out of an ELF header."""
    return data[offset] | (data[offset + 1] << 8)


def describe_elf(data: bytes) -> str:
    """One-line human description of `data`, for an error message.

    Never raises: this is what gets shown when something has already gone
    wrong, so it has to cope with whatever arrived — HTML, a PEM, or nothing.
    """
    if not data:
        return "empty file"
    if data[:4] != ELF_MAGIC:
        head = data[:16].decode("ascii", "replace").replace("\n", " ")
        return f"not an ELF (starts with {head!r}, {len(data)} bytes)"
    if len(data) < _MIN_ELF_HEADER:
        return f"truncated ELF header ({len(data)} bytes)"
    bits = {ELFCLASS32: "32-bit", 2: "64-bit"}.get(data[_EI_CLASS], f"class {data[_EI_CLASS]}")
    endian = {ELFDATA2LSB: "LSB", 2: "MSB"}.get(data[_EI_DATA], f"data {data[_EI_DATA]}")
    e_type = _E_TYPE_NAMES.get(_u16le(data, _E_TYPE), f"type {_u16le(data, _E_TYPE)}")
    machine = _E_MACHINE_NAMES.get(_u16le(data, _E_MACHINE), f"machine {_u16le(data, _E_MACHINE)}")
    return f"ELF {bits} {endian} {e_type} {machine}, {len(data)} bytes"


def is_mips_elf(data: bytes, *, exec_only: bool = False) -> bool:
    """Whether `data` is a 32-bit little-endian MIPS ELF we can patch.

    Args:
        exec_only: Require `ET_EXEC`. See `assert_mips_elf` for why this
            matters — it is not a style preference.
    """
    if len(data) < _MIN_ELF_HEADER or data[:4] != ELF_MAGIC:
        return False
    if data[_EI_CLASS] != ELFCLASS32 or data[_EI_DATA] != ELFDATA2LSB:
        return False
    if _u16le(data, _E_MACHINE) != EM_MIPS:
        return False
    e_type = _u16le(data, _E_TYPE)
    if exec_only:
        return e_type == ET_EXEC
    return e_type in (ET_EXEC, ET_DYN)


def assert_mips_elf(data: bytes, what: str, *, exec_only: bool = False) -> None:
    """Raise unless `data` is a MIPS32 little-endian ELF.

    Every Ingenic binary this project patches is `ELF 32-bit LSB, MIPS,
    MIPS32 rel2` — verified against extracted T5 and D4SH firmware.

    Args:
        exec_only: Reject a position-independent executable. `patchers/mqtt.py`
            and `patchers/cloud.py` convert a symbol's virtual address to a file
            offset with `vaddr - MIPS_ELF_BASE` (0x400000), which is only true
            for a fixed-load-address ET_EXEC. Handed a PIE binary they would
            compute a wrong offset and patch it WITHOUT complaining, so this
            flag is what turns a silent mis-patch into a refusal.

    Raises:
        ValueError: Naming `what` and what actually arrived, so a transport
            failure reads as one instead of as a firmware mismatch.
    """
    if is_mips_elf(data, exec_only=exec_only):
        return
    wanted = "MIPS32 little-endian ELF executable"
    if exec_only:
        wanted += " with a fixed load address (not PIE)"
    raise ValueError(f"{what} is not a {wanted} — got {describe_elf(data)}")


def is_arm_elf(data: bytes, *, exec_only: bool = False) -> bool:
    """Whether `data` is a 32-bit little-endian ARM ELF we can patch.

    Args:
        exec_only: Require `ET_EXEC`. See `assert_arm_elf` for why.
    """
    if len(data) < _MIN_ELF_HEADER or data[:4] != ELF_MAGIC:
        return False
    if data[_EI_CLASS] != ELFCLASS32 or data[_EI_DATA] != ELFDATA2LSB:
        return False
    if _u16le(data, _E_MACHINE) != EM_ARM:
        return False
    e_type = _u16le(data, _E_TYPE)
    if exec_only:
        return e_type == ET_EXEC
    return e_type in (ET_EXEC, ET_DYN)


def assert_arm_elf(data: bytes, what: str, *, exec_only: bool = False) -> None:
    """Raise unless `data` is an ARM 32-bit little-endian ELF.

    The ARM twin of `assert_mips_elf`, and `exec_only` carries the same weight:
    a virtual address is converted to a file offset with `vaddr - 0x10000`,
    which only holds for a fixed-load-address ET_EXEC. A PIE binary would be
    patched at a wrong offset without complaining.

    Args:
        what: Named in the error, so the message says which download failed.

    Raises:
        ValueError: with what the header actually said (`describe_elf`).
    """
    if is_arm_elf(data, exec_only=exec_only):
        return
    wanted = "ARM 32-bit little-endian ELF executable"
    if exec_only:
        wanted += " with a fixed load address (not PIE)"
    raise ValueError(f"{what} is not a {wanted} — got {describe_elf(data)}")


def elf_arch(data: bytes) -> str:
    """Return ``"mips"`` or ``"arm"`` for a 32-bit LE ELF, or ``""``."""
    if len(data) < _MIN_ELF_HEADER or data[:4] != ELF_MAGIC:
        return ""
    return {EM_MIPS: "mips", EM_ARM: "arm"}.get(_u16le(data, _E_MACHINE), "")


def assert_ca_bundle(data: bytes, what: str) -> None:
    """Raise unless `data` looks like a PEM certificate bundle.

    Guards the CA patcher, whose input is the device's Mozilla bundle rather
    than an ELF. The check exists because the alternative is worse than a
    failed patch: treating an empty download as "this device has no CA bundle"
    and writing a file containing only our self-signed certificate would leave
    the device unable to verify ANY other TLS peer. Every device has a bundle
    (226 KB on a T5, 11 KB on a D4SH), so an empty read means the httpd did not
    serve it — a transport failure, not a device without certificates.

    Raises:
        ValueError: If `data` contains no PEM certificate.
    """
    if data and b"-----BEGIN CERTIFICATE-----" in data:
        return
    if not data or not data.strip():
        detail = "empty"
    else:
        head = data[:40].decode("ascii", "replace").replace("\n", " ")
        detail = f"no PEM certificate found (starts with {head!r}, {len(data)} bytes)"
    raise ValueError(f"{what} does not look like a CA bundle — {detail}")


def assert_download_plausible(data: bytes, what: str, min_bytes: int = 1024) -> None:
    """Raise if a download is too small or is obviously an HTTP error page.

    Separate from the format checks so that "busybox httpd answered 200 with a
    directory listing" is reported as the transport problem it is, rather than
    surfacing later as "not a MIPS ELF" and sending the reader after the wrong
    cause.

    Raises:
        ValueError: If `data` is short or begins with markup.
    """
    if len(data) < min_bytes:
        raise ValueError(f"{what} download is only {len(data)} bytes — expected at least "
                         f"{min_bytes}; the device's httpd probably did not serve the file")
    head = data[:64].lstrip()[:16].lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        raise ValueError(f"{what} download is an HTML page, not the file — "
                         "the device's httpd served an error or a directory listing")
