import os

from petkit_local.media import layout


def test_device_folder_name_uses_display_name():
    assert layout.device_folder_name("t5", 42) == "Purobot Max Pro 2 (T5 42)"


def test_device_folder_name_falls_back_to_upper_type():
    assert layout.device_folder_name("zzz", 1) == "Unknown (ZZZ 1)"


def test_generated_names_are_url_safe():
    """HA's media browser builds its URL from the path without escaping it,
    so a '#' would truncate playback at the fragment."""
    for name in (layout.device_folder_name("t5", 42),
                 layout.build_media_path("/m", "t5", 42, "fullVideo", 1784726530,
                                         "Toilet visit", "mp4", pet_name="a#b&c%d")):
        for ch in "#&%":
            assert ch not in name, f"{ch!r} must not appear in {name!r}"


def test_role_folder_mapping():
    assert layout.role_folder("fullVideo") == "Playback"
    assert layout.role_folder("highLight") == "Highlight"
    # EVENT_PREVIEW is a single poster, NOT the waste gallery
    assert layout.role_folder("eventImage") == "Snapshots"
    # SHIT_PICTURE is the app's "Check waste" set
    assert layout.role_folder("wasteCheck") == "Waste"
    assert layout.role_folder("dynamicVideo") == "Clips"
    assert layout.role_folder("cloudDouble") == "Timelapse"
    assert layout.role_folder("unknownType") == "Other"


def test_media_filename_label_prefers_content_flags():
    # petEvent alone = the pet was seen; only toiletEvent means a visit
    assert layout.media_filename_label({"petEvent": True}, "cleaning") == "Appeared"
    assert layout.media_filename_label({"toiletEvent": True}) == "Toilet visit"
    assert layout.media_filename_label({"petEvent": True, "toiletEvent": True}) == "Toilet visit"
    assert layout.media_filename_label({"cleanEvent": True}) == "Cleaning"
    assert layout.media_filename_label({"cvrEvent": True}) == "Motion"


def test_media_filename_label_falls_back_to_event_kind():
    assert layout.media_filename_label(None, "toilet_visit") == "Toilet visit"
    assert layout.media_filename_label({}, "error") == "Error"
    assert layout.media_filename_label({}, None) == "Event"


def test_build_media_path_shape():
    p = layout.build_media_path(
        "/media/petkit", "t5", 1, "highLight", 1784726530, "Toilet visit", "mp4",
    )
    assert p.startswith("/media/petkit/Purobot Max Pro 2 (T5 1)/Highlight/")
    assert p.endswith(".mp4")
    assert "2026" in p or "20" in p  # has a date segment


def test_build_media_path_with_pet_and_index():
    # `index` is a bare arrival number, NOT "(n of m)": the total isn't known
    # at upload time, so an "of m" was wrong for every photo but the last.
    p = layout.build_media_path(
        "/media/petkit", "t5", 1, "wasteCheck", 1784726530, "Cleaning", "jpg",
        pet_name="Mruczek", index=2,
    )
    assert "Mruczek" in p
    assert p.endswith(" 2.jpg")
    assert "of" not in os.path.basename(p)


def test_device_folder_name_for_the_real_device_is_pinned():
    """Regression pin for the sanitizer swap: the folder name is what HA's
    media browser shows and what existing files already live under, so it may
    not drift. This is the real T5 from the 2026-07-22 capture."""
    assert (layout.device_folder_name("t5", 10000001, "Purobot Max Pro 2")
            == "Purobot Max Pro 2 (T5 10000001)")


def test_generated_names_have_no_control_characters():
    """A newline in a device- or pet-supplied name would terminate the line in
    media/stitch.py's ffmpeg concat list and let the rest be read as another
    directive, so it must not reach a path in the first place."""
    p = layout.build_media_path(
        "/media/petkit", "t5", 1, "fullVideo", 1784726530, "Toilet visit", "mp4",
        pet_name="evil\nfile '/tmp/x'\r\x00",
    )
    for ch in "\n\r\x00":
        assert ch not in p
    assert layout.device_folder_name("t5", 1, "Dev\nice") == "Device (T5 1)"


def test_build_media_path_caps_an_absurd_name_but_keeps_the_extension():
    """The length cap applies to the stem only — losing the extension would
    stop HA dispatching playback on it."""
    p = layout.build_media_path(
        "/media/petkit", "t5", 1, "fullVideo", 1784726530, "Toilet visit", "mp4",
        pet_name="M" * 400,
    )
    name = os.path.basename(p)
    assert name.endswith(".mp4")
    assert len(name) <= 128


def test_build_media_path_sanitizes_unsafe_pet_name():
    p = layout.build_media_path(
        "/media/petkit", "t5", 1, "eventImage", 1784726530, "Toilet visit", "jpg",
        pet_name="../../etc/passwd",
    )
    assert ".." not in p
    assert "/etc/passwd" not in p
    # still exactly one file under the expected date directory
    parts = p.split("/")
    assert parts.count("etc") == 0
