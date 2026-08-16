from petkit_local.mqtt.topics import (
    parse_topic, event_reply_topic, service_topic, ota_upgrade_topic,
)


def test_parse_event_property_post():
    p = parse_topic("/sys/a1PK/d_t5_SN1/thing/event/property/post")
    assert p is not None
    assert p.product_key == "a1PK"
    assert p.device_name == "d_t5_SN1"
    assert p.category == "event"
    assert p.detail == "property"


def test_parse_event_custom():
    p = parse_topic("/sys/a1PK/d_t5_SN1/thing/event/work_start/post")
    assert p.category == "event" and p.detail == "work_start"


def test_parse_service():
    p = parse_topic("/sys/a1PK/d_t5_SN1/thing/service/property/set")
    assert p.category == "service" and p.detail == "property/set"


def test_post_reply_is_not_parsed_as_event():
    # The bridge is subscribed to '#' and publishes replies; a reply must not
    # re-parse as an inbound event (which would loop).
    assert parse_topic("/sys/a1PK/d_t5_SN1/thing/event/property/post_reply") is None


def test_parse_ota_inform():
    p = parse_topic("/ota/device/inform/a1PK/d_t5_SN1")
    assert p.category == "ota" and p.detail == "inform"


def test_unknown_topic_returns_none():
    assert parse_topic("/random/nonsense") is None


def test_topic_builders_roundtrip():
    assert event_reply_topic("PK", "DN", "property") == "/sys/PK/DN/thing/event/property/post_reply"
    assert service_topic("PK", "DN", "property/set") == "/sys/PK/DN/thing/service/property/set"
    assert ota_upgrade_topic("PK", "DN") == "/ota/device/upgrade/PK/DN"
