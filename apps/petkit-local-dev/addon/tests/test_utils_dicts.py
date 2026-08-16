from petkit_local.utils.dicts import dig, dig_path, first_of


def test_dig_descends_nested_mappings():
    body = {"wifi": {"rsq": -51, "ssid": "No Signal"}}
    assert dig(body, "wifi", "rsq") == -51
    assert dig(body, "wifi") == {"rsq": -51, "ssid": "No Signal"}


def test_dig_missing_intermediate_level_returns_default():
    body = {"device": {"sw": 1}}
    assert dig(body, "wifi", "rsq") is None
    assert dig(body, "wifi", "rsq", default="?") == "?"
    assert dig(body, "device", "missing", default={}) == {}


def test_dig_non_mapping_intermediate_returns_default():
    # A scalar where a sub-object was expected: the device does send flat
    # values under keys that are nested objects on other firmwares.
    assert dig({"litter": 3119}, "litter", "weight", default=0) == 0
    assert dig({"litter": "3119"}, "litter", "weight", default=0) == 0
    assert dig({"err": ["DC"]}, "err", "DC", default=0) == 0
    assert dig({"a": {"b": True}}, "a", "b", "c", default="x") == "x"


def test_dig_explicit_none_is_returned_not_defaulted():
    # Key present with an explicit null is a reported value, not a miss.
    body = {"petWeight": None}
    assert dig(body, "petWeight") is None
    assert dig(body, "petWeight", default="MISS") is None
    assert dig(body, "absent", default="MISS") == "MISS"


def test_dig_through_explicit_none_returns_default():
    # None is not a mapping, so it cannot be descended into.
    assert dig({"wifi": None}, "wifi", "rsq", default="MISS") == "MISS"


def test_dig_with_no_keys_returns_input_unchanged():
    body = {"a": 1}
    assert dig(body) is body
    assert dig(None) is None
    assert dig("scalar") == "scalar"
    assert dig(None, default="MISS") is None


def test_dig_on_non_mapping_root():
    assert dig(None, "a") is None
    assert dig("text", "a", default="MISS") == "MISS"
    assert dig([1, 2], "a", default="MISS") == "MISS"


def test_dig_does_not_index_lists_by_integer():
    # Deliberate non-feature: no call site walks a list, and treating an int
    # as an index would make a dict with integer keys ambiguous.
    assert dig({"items": [10, 20]}, "items", 0, default="MISS") == "MISS"
    # Integer keys still work as plain mapping keys.
    assert dig({"items": {0: 10}}, "items", 0) == 10


def test_dig_path_dotted_lookup():
    doc = {"state": {"boxState": 1}, "capabilities": {"fullVideo": True}}
    assert dig_path(doc, "state.boxState") == 1
    assert dig_path(doc, "capabilities.fullVideo") is True
    assert dig_path(doc, "state") == {"boxState": 1}


def test_dig_path_missing_and_non_mapping():
    doc = {"state": {"boxState": 1}}
    assert dig_path(doc, "state.sandPercent") is None
    assert dig_path(doc, "settings.lightMode", default="MISS") == "MISS"
    assert dig_path(doc, "state.boxState.deep", default="MISS") == "MISS"


def test_dig_path_empty_path_returns_default():
    # An entity with no value_path has no value — it is not the whole document.
    doc = {"state": {"boxState": 1}}
    assert dig_path(doc, "") is None
    assert dig_path(doc, "", default="MISS") == "MISS"


def test_dig_path_preserves_explicit_none():
    doc = {"state": {"errorMsg": None}}
    assert dig_path(doc, "state.errorMsg", default="MISS") is None


def test_first_of_preference_order():
    form = {"event_id": "b", "eventId": "a"}
    assert first_of(form, "eventId", "event_id") == "a"
    assert first_of(form, "event_id", "eventId") == "b"


def test_first_of_skips_none_and_empty_string():
    # The device pads the spellings it did not use rather than omitting them.
    form = {"eventId": None, "event_id": "", "eventid": "3_10000001_1784741818"}
    assert first_of(form, "eventId", "event_id", "eventid") == "3_10000001_1784741818"


def test_first_of_returns_default_when_no_candidate_has_content():
    form = {"eventId": None, "event_id": ""}
    assert first_of(form, "eventId", "event_id") is None
    assert first_of(form, "eventId", "event_id", default="") == ""
    assert first_of(form, "absent", default="MISS") == "MISS"


def test_first_of_keeps_falsy_but_real_values():
    assert first_of({"encrypt": 0}, "encrypt", default="1") == 0
    assert first_of({"petEvent": False}, "petEvent", default="MISS") is False
    assert first_of({"count": 0.0}, "count", default="MISS") == 0.0


def test_first_of_with_no_keys_or_non_mapping_input():
    assert first_of({"a": 1}) is None
    assert first_of({"a": 1}, default="MISS") == "MISS"
    assert first_of(None, "a", default="MISS") == "MISS"
    assert first_of("text", "a", default="MISS") == "MISS"


def test_first_of_does_not_descend():
    assert first_of({"content": {"pet_weight": 3}}, "pet_weight", default="MISS") == "MISS"


def test_matches_existing_helper_behaviour_on_real_payload():
    # Exact nested shape from the real T5 state report, checked against what
    # _safe_get / _resolve_path / _first return today for the same inputs.
    body = {
        "litter": {"weight": 3119, "percent": 100},
        "wifi": {"rsq": -51},
        "device": {"sw": 1, "pet_in_time": 0},
        "workState": None,
    }
    assert dig(body, "wifi", default={}) == {"rsq": -51}
    assert dig(body, "workState", default=None) is None
    assert dig(body, "feedState", default={}) == {}
    assert dig(body, "device", "pet_in_time") == 0
    assert dig_path({"state": body}, "state.litter.percent") == 100
    assert first_of(body, "workState", "work_state", default={}) == {}
