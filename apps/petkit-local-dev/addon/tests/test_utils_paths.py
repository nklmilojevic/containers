import os
import tempfile

from petkit_local.utils.paths import (
    UnsafePathError,
    safe_join,
    sanitize_filename,
)


def _raises(fn, *args, **kwargs) -> UnsafePathError:
    try:
        fn(*args, **kwargs)
    except UnsafePathError as e:
        return e
    raise AssertionError(f"expected UnsafePathError, got a result from {fn.__name__}")


# --- safe_join ---------------------------------------------------------------

def test_safe_join_normal_path_stays_inside_root():
    with tempfile.TemporaryDirectory() as tmp:
        p = safe_join(tmp, "t5/10000001/fullVideo/clip.mp4")
        assert p == os.path.join(os.path.realpath(tmp), "t5", "10000001", "fullVideo", "clip.mp4")


def test_safe_join_accepts_multiple_segments():
    with tempfile.TemporaryDirectory() as tmp:
        assert safe_join(tmp, "a", "b", "c.jpg") == os.path.join(os.path.realpath(tmp), "a/b/c.jpg")


def test_safe_join_root_itself_is_allowed():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.realpath(tmp)
        assert safe_join(tmp) == root
        assert safe_join(tmp, "") == root
        assert safe_join(tmp, ".") == root
        assert safe_join(tmp, "./") == root


def test_safe_join_rejects_dotdot_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "media")
        os.makedirs(root)
        _raises(safe_join, root, "../../../etc/passwd")
        _raises(safe_join, root, "..")
        _raises(safe_join, root, "a/../../outside")
        _raises(safe_join, root, "a", "..", "..", "outside")


def test_safe_join_dotdot_inside_root_is_fine():
    with tempfile.TemporaryDirectory() as tmp:
        assert safe_join(tmp, "a/b/../c.txt") == os.path.join(os.path.realpath(tmp), "a/c.txt")


def test_safe_join_absolute_segment_is_treated_as_relative():
    """This is the http/bucket.py case: `PUT /etc/x` must land under the media
    root, not at /etc/x."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.realpath(tmp)
        assert safe_join(tmp, "/etc/passwd") == os.path.join(root, "etc/passwd")
        assert safe_join(tmp, "//etc//passwd") == os.path.join(root, "etc/passwd")


def test_safe_join_rejects_backslash_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "media")
        os.makedirs(root)
        _raises(safe_join, root, r"..\..\etc\passwd")
        assert safe_join(root, r"a\b.txt") == os.path.join(os.path.realpath(root), "a/b.txt")


def test_safe_join_rejects_symlink_escape():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "media")
        outside = os.path.join(tmp, "secrets")
        os.makedirs(root)
        os.makedirs(outside)
        with open(os.path.join(outside, "key.txt"), "w") as f:
            f.write("secret")
        os.symlink(outside, os.path.join(root, "link"))

        _raises(safe_join, root, "link/key.txt")
        _raises(safe_join, root, "link")


def test_safe_join_allows_symlink_that_stays_inside_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "media")
        real = os.path.join(root, "real")
        os.makedirs(real)
        os.symlink(real, os.path.join(root, "link"))
        assert safe_join(root, "link/clip.mp4") == os.path.join(os.path.realpath(real), "clip.mp4")


def test_safe_join_rejects_prefix_sibling_directory():
    """String-prefix containment checks accept /media/petkit-evil as being
    inside /media/petkit — component comparison must not."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "petkit")
        evil = os.path.join(tmp, "petkit-evil")
        os.makedirs(root)
        os.makedirs(evil)
        _raises(safe_join, root, "../petkit-evil/x.txt")
        _raises(safe_join, root, "../petkit-evil")


def test_safe_join_root_symlink_is_resolved():
    """A symlinked root (e.g. /media -> /mnt/media) must not make every
    resolved candidate look like an escape."""
    with tempfile.TemporaryDirectory() as tmp:
        real = os.path.join(tmp, "real")
        os.makedirs(real)
        link = os.path.join(tmp, "link")
        os.symlink(real, link)
        assert safe_join(link, "a.txt") == os.path.join(os.path.realpath(real), "a.txt")


def test_safe_join_rejects_nul_byte():
    with tempfile.TemporaryDirectory() as tmp:
        _raises(safe_join, tmp, "a\x00b")


def test_unsafe_path_error_is_a_value_error():
    assert issubclass(UnsafePathError, ValueError)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            safe_join(tmp, "../..")
        except ValueError:
            pass
        else:
            raise AssertionError("UnsafePathError must be catchable as ValueError")


# --- sanitize_filename -------------------------------------------------------

def test_sanitize_filename_keeps_ordinary_names():
    assert sanitize_filename("14-22-09 Toilet visit.mp4") == "14-22-09 Toilet visit.mp4"


def test_sanitize_filename_strips_separators():
    r = sanitize_filename("../../etc/passwd")
    assert r == "etc-passwd"
    assert "/" not in r and "\\" not in r and ".." not in r


def test_sanitize_filename_result_is_a_single_component():
    for name in ("a/b/c", r"a\b\c", "/etc/passwd", "..", "../.."):
        r = sanitize_filename(name)
        assert os.path.dirname(r) == "", r
        assert os.path.basename(r) == r, r
        assert os.sep not in r, r


def test_sanitize_filename_strips_leading_dots():
    assert sanitize_filename(".bashrc") == "bashrc"
    assert sanitize_filename("...hidden") == "hidden"
    assert not sanitize_filename(".....").startswith(".")


def test_sanitize_filename_strips_control_characters():
    r = sanitize_filename("visit\x00\x01\x07\x1f\x7f2026.mp4")
    assert r == "visit2026.mp4"


def test_sanitize_filename_strips_newlines():
    """An embedded newline would inject a second directive into the ffmpeg
    concat list media/stitch.py writes as `file '<path>'`."""
    r = sanitize_filename("clip.mp4'\nfile '/etc/shadow'\n")
    assert "\n" not in r and "\r" not in r
    assert "/" not in r
    r2 = sanitize_filename("a\r\nb\tc.mp4")
    assert r2 == "abc.mp4"


def test_sanitize_filename_strips_url_hostile_characters():
    r = sanitize_filename("cat#1 & 50% done.mp4")
    for ch in "#&%":
        assert ch not in r


def test_sanitize_filename_empty_falls_back():
    assert sanitize_filename("") == "file"
    assert sanitize_filename("   ") == "file"
    assert sanitize_filename("///") == "file"
    assert sanitize_filename("\x00\x01\n") == "file"
    assert sanitize_filename("", fallback="unnamed") == "unnamed"


def test_sanitize_filename_caps_length_and_keeps_extension():
    r = sanitize_filename("a" * 300 + ".mp4")
    assert len(r) <= 120
    assert r.endswith(".mp4")
    assert r == "a" * 116 + ".mp4"


def test_sanitize_filename_respects_custom_max_length():
    r = sanitize_filename("b" * 50 + ".jpeg", max_length=20)
    assert len(r) == 20
    assert r.endswith(".jpeg")


def test_sanitize_filename_long_name_without_extension():
    r = sanitize_filename("c" * 300)
    assert r == "c" * 120


def test_sanitize_filename_ignores_a_dot_that_is_not_an_extension():
    """A long trailing segment after a dot is part of the name, not a suffix —
    keeping it would eat the whole budget."""
    r = sanitize_filename("d" * 200 + "." + "e" * 40, max_length=30)
    assert len(r) == 30
    assert r == "d" * 30


def test_sanitize_filename_composes_with_safe_join():
    with tempfile.TemporaryDirectory() as tmp:
        pet_name = "../../root/.ssh/authorized_keys"
        p = safe_join(tmp, sanitize_filename(pet_name))
        assert os.path.dirname(p) == os.path.realpath(tmp)
