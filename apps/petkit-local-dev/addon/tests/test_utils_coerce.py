"""Tests for petkit_local.utils.coerce.

The functions parse device-controlled input, so the rejection cases matter as
much as the accepted ones: anything that raises here becomes an HTTP 500 in a
request handler.
"""
import tempfile
from pathlib import Path

from petkit_local.utils.coerce import to_bool, to_float, to_int

# Inputs no coercion should ever choke on. Reused by the "nothing raises"
# sweep at the bottom.
HOSTILE_VALUES = [
    None, "", "   ", "\n\t ", "abc", "0x10", "1e", "--1", "1.2.3", "+", "-",
    "1_0", "1_000.5", "١٢٣", "inf", "-inf", "Infinity", "nan", "NaN",
    "1e400", "9" * 5000, float("inf"), float("-inf"), float("nan"), 10 ** 400,
    [], {}, (), set(), b"1", bytearray(b"1"), object(), Path("/tmp"),
    {"id": 1}, [1, 2], "ON", "OFF", "true", 0, 1, 2, -1, 1.5, True, False,
]


# --- to_int -----------------------------------------------------------------

def test_to_int_accepts_ints_and_bools():
    assert to_int(42, None) == 42
    assert to_int(-42, None) == -42
    assert to_int(0, None) == 0
    assert to_int(True, None) == 1
    assert to_int(False, None) == 0


def test_to_int_truncates_floats_toward_zero():
    assert to_int(1.9, None) == 1
    assert to_int(-1.9, None) == -1
    assert to_int(0.4, None) == 0
    assert to_int(-0.4, None) == 0


def test_to_int_accepts_numeric_strings_with_whitespace():
    assert to_int("  7  ", None) == 7
    assert to_int("\n7\t", None) == 7
    assert to_int("+7", None) == 7
    assert to_int("-7", None) == -7
    assert to_int("007", None) == 7


def test_to_int_accepts_float_shaped_strings_and_truncates():
    assert to_int(" 1.9 ", None) == 1
    assert to_int("-1.9", None) == -1
    assert to_int("2e3", None) == 2000
    assert to_int(".5", None) == 0


def test_to_int_rejects_none_and_empty():
    assert to_int(None, None) is None
    assert to_int("", None) is None
    assert to_int("   ", None) is None
    assert to_int(None, 0) == 0


def test_to_int_rejects_non_numeric_text():
    for bad in ("abc", "0x10", "1e", "--1", "1.2.3", "7 7", "+", "-"):
        assert to_int(bad, None) is None, bad


def test_to_int_rejects_underscore_digit_separators():
    """int("1_0") == 10 in Python. The device sends bare tokens like
    `4_10000001_1784743819` (events/ingest.py), which must never read as a
    number."""
    assert to_int("1_0", None) is None
    assert to_int("4_10000001_1784743819", None) is None
    assert to_int(" 1_000 ", None) is None


def test_to_int_rejects_non_ascii_digits():
    # int("١٢٣") == 123 in Python; a device id is ASCII or it is garbage.
    assert to_int("١٢٣", None) is None


def test_to_int_rejects_infinity_and_nan():
    assert to_int(float("inf"), None) is None
    assert to_int(float("-inf"), None) is None
    assert to_int(float("nan"), None) is None
    for bad in ("inf", "-inf", "Infinity", "nan", "NaN"):
        assert to_int(bad, None) is None, bad
    # Overflows to inf via float(), so it must not reach int() and raise.
    assert to_int("1e400", None) is None


def test_to_int_rejects_oversized_digit_string():
    """CPython raises ValueError above sys.int_max_str_digits (4300)."""
    assert to_int("9" * 5000, None) is None


def test_to_int_rejects_other_types():
    for bad in ([], {}, (), set(), b"1", bytearray(b"1"), object()):
        assert to_int(bad, None) is None, type(bad)


def test_to_int_returns_the_given_default():
    assert to_int("garbage", 0) == 0
    assert to_int("garbage", -1) == -1
    assert to_int("garbage", None) is None


# --- to_float ---------------------------------------------------------------

def test_to_float_accepts_numbers_and_bools():
    assert to_float(1.5, None) == 1.5
    assert to_float(-1.5, None) == -1.5
    assert to_float(3, None) == 3.0
    assert to_float(True, None) == 1.0
    assert to_float(False, None) == 0.0


def test_to_float_accepts_numeric_strings_with_whitespace():
    assert to_float("  1.5  ", None) == 1.5
    assert to_float("-1.5", None) == -1.5
    assert to_float("+1.5", None) == 1.5
    assert to_float("3", None) == 3.0
    assert to_float("2e3", None) == 2000.0
    assert to_float(".5", None) == 0.5
    assert to_float("1.", None) == 1.0


def test_to_float_rejects_none_and_empty():
    assert to_float(None, None) is None
    assert to_float("", None) is None
    assert to_float("   ", None) is None
    assert to_float(None, 0.0) == 0.0


def test_to_float_rejects_infinity_and_nan():
    """float("inf") parses, but json.dumps writes bare Infinity/NaN — invalid
    JSON that nothing downstream can read back."""
    for bad in ("inf", "-inf", "Infinity", "infinity", "nan", "NaN", "1e400"):
        assert to_float(bad, None) is None, bad
    assert to_float(float("inf"), None) is None
    assert to_float(float("-inf"), None) is None
    assert to_float(float("nan"), None) is None


def test_to_float_rejects_underscore_digit_separators():
    assert to_float("1_0", None) is None
    assert to_float("1_000.5", None) is None
    assert to_float("4_10000001_1784743819", None) is None


def test_to_float_rejects_non_ascii_digits():
    assert to_float("١٢٣", None) is None


def test_to_float_rejects_int_too_large_for_a_float():
    # float(10**400) raises OverflowError; ints are unbounded, floats are not.
    assert to_float(10 ** 400, None) is None


def test_to_float_rejects_non_numeric_text_and_other_types():
    for bad in ("abc", "1.2.3", "--1", [], {}, b"1", object()):
        assert to_float(bad, None) is None


def test_to_float_returns_the_given_default():
    assert to_float("garbage", 0.0) == 0.0
    assert to_float("garbage", -1.0) == -1.0


# --- to_bool ----------------------------------------------------------------

def test_to_bool_accepts_real_bools():
    assert to_bool(True, None) is True
    assert to_bool(False, None) is False


def test_to_bool_accepts_zero_and_one():
    assert to_bool(1, None) is True
    assert to_bool(0, None) is False
    assert to_bool(1.0, None) is True
    assert to_bool(0.0, None) is False


def test_to_bool_accepts_ha_payload_strings():
    """HA publishes ha/discovery.py's payload_on/payload_off ("ON"/"OFF")."""
    assert to_bool("ON", None) is True
    assert to_bool("OFF", None) is False
    assert to_bool(" on ", None) is True
    assert to_bool("Off", None) is False


def test_to_bool_accepts_the_other_string_forms_in_use():
    for text in ("1", "true", "TRUE", "True", "yes", "  Yes  "):
        assert to_bool(text, None) is True, text
    for text in ("0", "false", "FALSE", "False", "no", "  No  "):
        assert to_bool(text, None) is False, text


def test_to_bool_rejects_numbers_other_than_zero_and_one():
    """A field carrying 2 is a mode enum, not a switch — surface it instead of
    silently reading it as True."""
    for bad in (2, -1, 0.5, 255):
        assert to_bool(bad, None) is None, bad


def test_to_bool_rejects_none_empty_and_unknown_text():
    for bad in (None, "", "   ", "maybe", "onn", "2", "1.0", "y", "n"):
        assert to_bool(bad, None) is None, bad


def test_to_bool_rejects_other_types():
    for bad in ([], {}, (), b"1", object()):
        assert to_bool(bad, None) is None, type(bad)


def test_to_bool_returns_the_given_default():
    # Reproduces _coerce_switch's "anything unrecognised is off" by choice of
    # default, rather than baking that policy into the helper.
    assert to_bool("garbage", False) is False
    assert to_bool("garbage", True) is True


# --- parity with the helpers this module replaces ---------------------------

def test_superset_of_ha_commands_helpers():
    from petkit_local.ha.commands import _coerce_number, _coerce_switch

    for payload in ("ON", "TRUE", "1", "on", "true"):
        assert to_bool(payload, False) is bool(_coerce_switch(payload)), payload
    for payload in ("OFF", "FALSE", "0", "garbage"):
        assert to_bool(payload, False) is bool(_coerce_switch(payload)), payload

    for payload in ("3", " 3 ", "-3", "0"):
        assert to_int(payload, None) == _coerce_number(payload), payload
    for payload in ("1.5", "-0.25", "2e3"):
        assert to_float(payload, None) == float(_coerce_number(payload)), payload
    for payload in ("abc", "", "  "):
        assert _coerce_number(payload) is None
        assert to_int(payload, None) is None
        assert to_float(payload, None) is None


def test_underscore_narrowing_is_deliberate():
    """The one place these helpers accept LESS than the bare `int()`/`float()`
    calls they replaced: underscore digit separators. `events/normalize.py` has to
    recognise the device's bare token `4_10000001_1784743819` as NOT a number,
    which is exactly what `float()` gets wrong."""
    assert float("1_0") == 10.0        # what the replaced helpers did
    assert to_int("1_0", None) is None
    assert to_float("1_0", None) is None


# --- the contract that matters: nothing raises ------------------------------

def test_nothing_raises_on_hostile_input():
    for value in HOSTILE_VALUES:
        to_int(value, None)
        to_int(value, 0)
        to_float(value, None)
        to_float(value, 0.0)
        to_bool(value, None)
        to_bool(value, False)


def test_handler_id_pattern_never_raises():
    """The bare `int(x_dev.get("id", 0))` in http/handlers/* answers HTTP 500
    when a device sends a non-numeric id."""
    for header in ({}, {"id": "abc"}, {"id": ""}, {"id": None}, {"id": "12"},
                   {"id": "1_0"}, {"id": "9" * 5000}):
        assert isinstance(to_int(header.get("id"), 0), int)
    assert to_int({"id": "12"}.get("id"), 0) == 12


def test_reading_a_scalar_off_disk_never_raises():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "id.txt"
        path.write_text("  10000001\n")
        assert to_int(path.read_text(), 0) == 10000001

        path.write_text("not a number")
        assert to_int(path.read_text(), 0) == 0
