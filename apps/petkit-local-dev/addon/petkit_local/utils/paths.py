"""Filesystem path safety helpers — containment-checked joins and filename
sanitization.

Every endpoint that turns a device- or user-supplied string into a path had
grown its own guard (`web/panel.py`, `ai/pets.py`, `media/layout.py`,
`http/server.py`, `http/handlers/discern.py`) and one of them —
`http/bucket.py`, which sits on an unauthenticated port and only did
`path.lstrip("/")` — had no guard against `..` at all, so a crafted upload key
could write outside the media root. This module is the single implementation
those call sites converge on: `safe_join` for "resolve untrusted input under a
root", `sanitize_filename` for "turn an arbitrary label into one safe path
component".
"""
from __future__ import annotations

import os
import re

# Path separators plus the characters that break either a filesystem or a URL.
# `#&%` are here (not just the filesystem-hostile set) because these names end
# up inside Home Assistant media-browser URLs, which are built WITHOUT escaping
# — a `#` truncates the URL at the fragment and playback silently fails. Same
# set as media/layout.py, which this replaces.
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|#&%]')

# Newlines, NUL, DEL and the rest of the C0/C1 control ranges. Removed, never
# substituted — see sanitize_filename's docstring for why this is the strict
# part of the function.
_CONTROL_CHARS = re.compile(r'[\x00-\x1f\x7f-\x9f]')

_DOT_RUNS = re.compile(r'\.{2,}')

# Long enough for the generated media names ("HH-MM-SS Toilet visit - Pet.mp4")
# while staying well under the 255-byte limit of ext4/APFS even after a caller
# appends a suffix, and under 255 *bytes* for multi-byte pet names too.
MAX_FILENAME_LENGTH = 120

# A dot this far into a name is part of the name, not an extension.
_MAX_EXTENSION_LENGTH = 12

FALLBACK_FILENAME = "file"


class UnsafePathError(ValueError):
    """A joined path resolved to somewhere outside its root.

    Subclasses `ValueError` so existing call sites that catch `ValueError`
    keep working.
    """


def _components(path: str) -> list[str]:
    """Split an absolute, normalized path into its path components."""
    drive, tail = os.path.splitdrive(path)
    parts = [p for p in tail.replace("\\", "/").split("/") if p]
    return ([drive] if drive else []) + parts


def safe_join(root: str | os.PathLike[str], *untrusted: str) -> str:
    """Join untrusted segments under `root` and verify the result stays inside.

    Both `root` and the candidate are fully resolved (`realpath`) before the
    containment check, so a symlink inside the tree cannot be used to step out
    of it. Untrusted segments are treated as relative even if they start with a
    separator (`/etc/passwd` becomes `<root>/etc/passwd`), backslashes are read
    as separators, and empty/`.` segments are dropped. `..` is not rewritten —
    it is allowed to resolve and then caught by the containment check, which is
    the only test that also covers symlinks.

    Args:
        untrusted: Path fragments from a request, a device payload or a
            filename. May themselves contain separators.

    Returns:
        The absolute resolved path. `root` itself is a valid result.

    Raises:
        UnsafePathError: The candidate resolved outside `root`, or an argument
            contained a NUL byte.
    """
    root_str = os.fspath(root)
    if "\x00" in root_str or any("\x00" in seg for seg in untrusted):
        raise UnsafePathError("path contains a NUL byte")

    root_real = os.path.realpath(root_str)

    relative_parts: list[str] = []
    for segment in untrusted:
        for part in segment.replace("\\", "/").split("/"):
            if part and part != ".":
                relative_parts.append(part)

    candidate = os.path.realpath(os.path.join(root_real, *relative_parts))

    # Component-wise, not `candidate.startswith(root)`: a string prefix test
    # accepts sibling directories that merely share a prefix, so
    # /media/petkit-evil would pass as "inside" /media/petkit.
    root_parts = _components(root_real)
    candidate_parts = _components(candidate)
    if candidate_parts[:len(root_parts)] != root_parts:
        raise UnsafePathError(f"path escapes root {root_real!r}: {'/'.join(relative_parts)!r}")

    return candidate


def sanitize_filename(
    name: str,
    *,
    fallback: str = FALLBACK_FILENAME,
    max_length: int = MAX_FILENAME_LENGTH,
) -> str:
    """Reduce an arbitrary label to a single safe path component.

    Control characters are removed outright, which is the strict part of this
    function and the reason it exists: device- and pet-supplied names become
    filenames that `media/stitch.py` writes verbatim into an ffmpeg concat list
    as ``file '<path>'``. Quotes in that list are escapable and are escaped by
    the caller, but a newline is not — it terminates the line and lets the rest
    of the name be read as another concat directive.

    The result contains no path separator, no `..` run and no leading dot, so
    it can neither traverse nor create a hidden file, and it is safe to place
    in a URL path (Home Assistant's media browser does not escape them).

    Args:
        fallback: Returned when nothing survives sanitization.
        max_length: Cap on the result; a file extension is preserved by
            truncating the stem rather than the tail.
    """
    name = _CONTROL_CHARS.sub("", name)
    name = _UNSAFE_CHARS.sub("-", name)
    name = _DOT_RUNS.sub("-", name)
    # Leading dots hide the file; a leading dash makes the name look like a
    # command-line flag to every tool the path is passed to (ffmpeg, ffprobe).
    name = name.strip(" .-")

    if not name:
        return fallback

    if len(name) > max_length:
        stem, extension = os.path.splitext(name)
        if len(extension) > _MAX_EXTENSION_LENGTH or len(extension) >= max_length:
            extension = ""
            stem = name
        name = (stem[:max_length - len(extension)] + extension).strip(" .-")

    return name or fallback
