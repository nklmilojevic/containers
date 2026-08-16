"""Friendly, human-readable media paths — one folder per device, sub-folder
per capability role, so uploaded clips/photos are browsable and playable in
the HA media browser instead of sitting as opaque encrypted blobs (see
http/bucket.py's hidden `.raw` staging dir + media/pipeline.py, which moves
files here once decrypted/remuxed).

    {root}/{Device Name} ({TYPE} {id})/
        Playback/   YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}].mp4        # fullVideo
        Timelapse/  YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}].mp4        # cloudDouble
        Clips/      YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}].mp4        # dynamicVideo
        Highlight/  YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}].mp4        # highLight
        Snapshots/  YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}].jpg        # eventImage
        Waste/      YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}] {n}.jpg    # wasteCheck
        Health/     YYYY-MM-DD/ HH-MM-SS {EventLabel}[ · {Pet}] {n}.jpg    # healthPic

No `#` before the id (see `device_folder_name`) and a BARE burst index rather
than "(n of m)" (see `build_media_path`) — both are constraints of the
consumers, not cosmetics.
"""
from __future__ import annotations

import os
from datetime import datetime

from petkit_local.events import codes
from petkit_local.utils.const import device_display_name
from petkit_local.utils.paths import sanitize_filename

# Media role (the `category` column, derived from moduleType by
# events/normalize.py::_MODULE_TYPE_TO_CATEGORY) -> folder name. NOT keyed by the
# STS capability: several roles share one capability, and they must not share
# a folder.
ROLE_FOLDERS = {
    "fullVideo": "Playback",
    "highLight": "Highlight",
    # ONE poster image per event (moduleType EVENT_PREVIEW). NOT the waste
    # gallery, which is `wasteCheck` below.
    "eventImage": "Snapshots",
    # The app's "Check waste" set, ~5 photos per cleaning (SHIT_PICTURE).
    "wasteCheck": "Waste",
    # The T5's stool-health-analysis photos (HEALTH_PRED).
    "healthPic": "Health",
    "dynamicVideo": "Clips",
    # Not an STS capability. Measured on real files: it stores ~1s of 528x528
    # footage per ~4s of wall clock, i.e. a ~4x TIME-LAPSE of the same span
    # the main recording covers — not a low-res mirror, which is why it looks
    # "sped up". Kept apart so it never mixes with the main stream. See
    # events/normalize.py::_MODULE_TYPE_TO_CATEGORY.
    "cloudDouble": "Timelapse",
}


def device_folder_name(device_type: str, petkit_id: int, device_name: str | None = None) -> str:
    """e.g. `Purobot Max Pro 2 (T5 10000001)`. Deliberately no `#` before the
    id — `utils.paths.sanitize_filename` strips it, because an unescaped `#`
    breaks Home Assistant's media-browser URLs."""
    label = device_name or device_display_name(device_type) or (device_type or "").upper()
    return sanitize_filename(f"{label} ({(device_type or '').upper()} {petkit_id})")


def role_folder(category: str) -> str:
    """Sub-folder for a media role, or "Other" for one we don't map yet.

    Deliberately never raises: an unrecognised category must still produce a
    browsable file (the loud complaint about it happens once, at ingest — see
    events/normalize.py::_resolve_category).
    """
    return ROLE_FOLDERS.get(category, "Other")


# content flags (as seen on file_info entries, mirrored on
# event_report.content — see events/normalize.py::classify_event_kind) -> a
# human label for the filename.
# Order matters: `toiletEvent` (the box was actually used) outranks
# `petEvent` (the pet was merely seen and recorded). A chunk from an
# "appeared" episode carries petEvent=1 with toiletEvent=0 for its whole
# length, so treating petEvent as a toilet visit mislabelled those files.
_EVENT_LABELS = (
    ("toiletEvent", "Toilet visit"),
    ("cleanEvent", "Cleaning"),
    ("cvrEvent", "Motion"),
    ("petEvent", "Appeared"),
)

_KIND_LABELS = {
    codes.KIND_TOILET: "Toilet visit", codes.KIND_CLEANING: "Cleaning",
    codes.KIND_PET: "Appeared", codes.KIND_MOTION: "Motion",
    codes.KIND_ERROR: "Error", codes.KIND_FEEDING: "Feeding",
}


def media_filename_label(content: dict | None = None, event_kind: str | None = None) -> str:
    """The human words that go in a media filename, e.g. "Toilet visit".

    Not `events/decode.py::event_label`, which builds the Timeline's rich
    sentence for one event code: this is the `{EventLabel}` of the path shapes
    in the module header, so it must stay short, stable and one of a handful of
    values — it is what a folder of files sorts and reads as.

    `content`'s per-file flags win over `event_kind` because they describe
    THIS file, whereas `event_kind` is the surrounding event's classification
    and can be coarser. Falls back to "Event" so a file always gets a name.
    """
    content = content or {}
    for flag, label in _EVENT_LABELS:
        if content.get(flag):
            return label
    return _KIND_LABELS.get(event_kind or "", "Event")


def build_media_path(media_root: str, device_type: str, petkit_id: int, category: str,
                      when: float | None, label: str, ext: str,
                      device_name: str | None = None, pet_name: str | None = None,
                      index: int | None = None) -> str:
    """Build the full friendly path for one media file. Does not create the
    file or its parent directories — the caller (media/pipeline.py) does
    that once the bytes are ready to write.

    `index` is a bare arrival number for a burst (waste/health galleries), e.g.
    `3` -> "... 3.jpg". It is deliberately NOT "(n of m)": the total isn't
    known at upload time (photos stream in), so an "of m" baked into the name
    was wrong for every photo but the last. The true "n / m" is shown at
    display time from the actual group (web/panel.py's carousel/badge)."""
    dt = datetime.fromtimestamp(when) if when else datetime.now()
    day = dt.strftime("%Y-%m-%d")
    stamp = dt.strftime("%H-%M-%S")

    name = f"{stamp} {label}"
    if pet_name:
        name += f" · {pet_name}"
    if index:
        name += f" {index}"
    # Sanitized before the extension is appended, so the length cap can never
    # eat the `.mp4`/`.jpg` the media browser dispatches playback on.
    name = sanitize_filename(name) + f".{ext.lstrip('.')}"

    device_dir = device_folder_name(device_type, petkit_id, device_name)
    return os.path.join(media_root, device_dir, role_folder(category), day, name)
