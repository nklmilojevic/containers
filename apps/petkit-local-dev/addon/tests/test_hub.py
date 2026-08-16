"""Tests for `web/hub.py` — the panel's in-memory event ring.

Volatile by design: the ring is capped, the per-device diagnostics and the
upstream counters are capped too, and a stalled panel tab must never stall
the HTTP server or the MQTT bridge.
"""
import json

from petkit_local.web.hub import EventHub


def test_hub_publish_and_recent():
    hub = EventHub(maxlen=5)
    for i in range(8):
        hub.publish("http", device_id=1, summary=f"e{i}")
    evs = hub.recent()
    assert len(evs) == 5  # ring capped
    assert evs[-1]["summary"] == "e7"


def test_hub_recent_filters_by_device():
    hub = EventHub()
    hub.publish("http", 1, "a")
    hub.publish("http", 2, "b")
    assert [e["summary"] for e in hub.recent(device_id=2)] == ["b"]


def test_hub_diag_records():
    hub = EventHub()
    hub.record_http(5, "POST", "/6/t5/dev_signup", 200)
    hub.set_state_report(5, {"sandPercent": 40})
    hub.record_mqtt(5, "/sys/pk/dn/thing/event/property/post", {"params": {"x": 1}})
    hub.record_connect(5, {"username": "d_t5_SN&pk", "ok": True})
    d = hub.diag(5)
    assert d["http_count"] == 1 and d["mqtt_count"] == 1
    assert d["last_state_report"]["body"]["sandPercent"] == 40
    assert d["last_property"]["payload"]["params"]["x"] == 1
    assert d["last_connect"]["ok"] is True


def test_mqtt_event_carries_an_expandable_detail():
    """A log row is expandable in the panel only when its event has a `detail`;
    an MQTT frame published without one shows a topic and nothing else."""
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/d_t5_SN/thing/event/property/post",
                    {"params": {"x": 1}}, client="d_t5_SN")
    ev = hub.recent()[-1]
    assert ev["summary"] == "from d_t5_SN: event/property/post"
    assert ev["detail"]["direction"] == "device → server"
    assert ev["detail"]["client"] == "d_t5_SN"
    assert ev["detail"]["topic"] == "/sys/pk/d_t5_SN/thing/event/property/post"
    assert json.loads(ev["detail"]["payload"]) == {"params": {"x": 1}}


def test_outbound_mqtt_is_logged_but_not_counted_as_device_traffic():
    """`mqtt_count` answers "is this device talking to us" — our own commands
    must not answer yes on its behalf."""
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/d_t5_SN/thing/service/property/set",
                    {"method": "thing.service.property.set"},
                    outbound=True, client="d_t5_SN")
    ev = hub.recent()[-1]
    assert ev["summary"] == "to d_t5_SN: service/property/set"
    assert ev["detail"]["direction"] == "server → device"
    assert hub.diag(5)["mqtt_count"] == 0
    assert "last_mqtt" not in hub.diag(5)


def test_a_relayed_cloud_frame_names_the_device_as_its_destination():
    """Proxy mode's downstream frames are outbound but not OURS.

    Passing the cloud as `client` rendered "to the real cloud" for a frame
    arriving FROM it — the direction read exactly backwards in the log.
    """
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/d_t5_SN/thing/service/start", {"method": "thing.service.start"},
                    outbound=True, client="d_t5_SN", origin="the real cloud")
    ev = hub.recent()[-1]
    assert ev["summary"] == "to d_t5_SN (relayed from the real cloud): service/start"
    assert ev["detail"]["direction"] == "the real cloud → server → device"
    assert ev["detail"]["origin"] == "the real cloud"


def test_a_wire_payload_is_decoded_not_repred():
    """Proxy mode relays the cloud's frame as bytes; `json.dumps` refuses those,
    and the repr fallback rendered `b'{...}'` — unreadable and no longer JSON
    for the panel to expand."""
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/d_t5_SN/thing/service/start", b'{"method": "thing.service.start"}',
                    outbound=True, client="d_t5_SN", origin="the real cloud")
    payload = hub.recent()[-1]["detail"]["payload"]
    assert json.loads(payload) == {"method": "thing.service.start"}


def test_mqtt_payload_is_capped():
    """The ring keeps these in memory and ships each one to every open browser."""
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/dn/thing/event/property/post", {"blob": "x" * 20_000})
    payload = hub.recent()[-1]["detail"]["payload"]
    assert len(payload) < 5000
    assert "truncated" in payload


def test_mqtt_payload_that_is_not_json_still_renders():
    hub = EventHub()
    hub.record_mqtt(5, "/sys/pk/dn/thing/event/x/post", {"o": object()})
    assert hub.recent()[-1]["detail"]["payload"]


def test_short_topic_leaves_an_unexpected_shape_alone():
    hub = EventHub()
    hub.record_mqtt(5, "some/other/topic", {})
    assert hub.recent()[-1]["summary"].endswith("some/other/topic")
