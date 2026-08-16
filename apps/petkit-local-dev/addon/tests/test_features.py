"""Tests for the added features: offline watchdog, device->HA setting sync,
capture mode, event/text entities, schedule text routing, W5 BLE parser."""
import json
import tempfile
from pathlib import Path

from petkit_local.devices.base import Device
from petkit_local.ha.categories import get_entities_for_device, get_setting_fields
from petkit_local.ha.commands import handle_ha_command
import base64

from petkit_local.ha.publisher import device_is_stale
from petkit_local.devices.ble import parse_w5_ble_response, _decode_w5_status
from petkit_local.utils.capture import capture_record


# --- offline watchdog ---

def test_never_seen_device_is_not_stale():
    d = Device(device_type="t5", petkit_id=1)
    assert device_is_stale(d, now=10_000, timeout=180) is False


def test_recently_seen_not_stale_but_old_is():
    d = Device(device_type="t5", petkit_id=1)
    d.last_heartbeat = 1000.0
    assert device_is_stale(d, now=1100, timeout=180) is False
    assert device_is_stale(d, now=1300, timeout=180) is True


def test_mqtt_traffic_counts_as_contact():
    """A device that gets onto the broker stops polling the HTTP heartbeat —
    confirmed on a T5, which went quiet over HTTP ~40s after its CONNECT. Judged
    on HTTP alone it looks dead, and the watchdog then clears `mqtt_connected`
    and sends its commands to a queue nothing will ever drain."""
    d = Device(device_type="t5", petkit_id=1)
    d.last_heartbeat = 1000.0
    d.last_mqtt = 1250.0
    assert device_is_stale(d, now=1300, timeout=180) is False
    assert device_is_stale(d, now=1500, timeout=180) is True


# --- device -> HA setting sync ---

def test_setting_fields_derived_from_entities():
    d = Device(device_type="t5", petkit_id=1)
    fields = get_setting_fields(d)
    assert "autoWork" in fields and "manualLock" in fields
    # non-settings sensors must not leak in
    assert "sandPercent" not in fields


# --- capture mode ---

def test_capture_writes_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        capture_record(tmp, "state_report", {"id": 1, "body": {"x": 2}})
        capture_record(tmp, "state_report", {"id": 1, "body": {"x": 3}})
        lines = (Path(tmp) / "state_report.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        rec = json.loads(lines[0])
        assert rec["id"] == 1 and rec["body"]["x"] == 2 and "ts" in rec


# --- event + text entities ---

def test_litter_has_event_and_text_entities():
    d = Device(device_type="t5", petkit_id=1)
    comps = {e.component for e in get_entities_for_device(d)}
    assert "event" in comps and "text" in comps


def test_text_schedule_routing_writes_config_and_pushes():
    d = Device(device_type="t5", petkit_id=1)
    idx = {e.unique_id_suffix: e for e in get_entities_for_device(d) if e.is_settable}
    ent = idx["cleaning_schedule"]
    schedule = json.dumps([{"time": 585, "type": 0}])
    res = handle_ha_command(d, ent, schedule)
    assert d.config["schedule"] == [{"time": 585, "type": 0}]
    assert res is not None
    suffix, envelope = res
    assert suffix == "property/set"
    assert "schedule" in envelope["params"]


def test_event_entity_is_not_settable():
    d = Device(device_type="t5", petkit_id=1)
    events = [e for e in get_entities_for_device(d) if e.component == "event"]
    assert events
    for e in events:
        assert not e.is_settable


# --- W5 BLE parser ---

def test_w5_structured_content():
    frag = parse_w5_ble_response({
        "powerStatus": 1, "runningStatus": 1,
        "warningWaterMissing": 0, "filterPercentage": 72,
    })
    assert frag["states"]["powerStatus"] == 1
    assert frag["consumables"]["filterPercentage"] == 72


def test_w5_binary_frame_decodes_real_layout():
    # cmd 230 DATA layout (W5/Parser.php parseDeviceStatus):
    # 0=power 1=mode 2=dnd 3=breakdown 4=waterMissing 5=filterWarn
    # 6-9=pumpRuntime 10=filterPercentage 11=runningStatus
    data = bytes([1, 2, 0, 0, 1, 0, 0, 0, 0, 0, 65, 1])
    b64 = base64.b64encode(data).decode()
    frag = parse_w5_ble_response({"device": {"mac": "AA"}, "payload": [{"cmd": 230, "data": b64}]})
    assert frag["states"]["powerStatus"] == 1
    assert frag["states"]["runningStatus"] == 1
    assert frag["states"]["warningWaterMissing"] == 1
    assert frag["consumables"]["filterPercentage"] == 65


def test_w5_data_is_urldecoded():
    # localkit sends urlencode(base64(bytes)); '+' -> '%2B' etc.
    data = bytes([1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 50, 0])
    import urllib.parse
    b64 = urllib.parse.quote(base64.b64encode(data).decode())
    frag = parse_w5_ble_response({"payload": [{"cmd": 230, "data": b64}]})
    assert frag["consumables"]["filterPercentage"] == 50


def test_w5_ignores_non_status_cmd():
    # a battery frame (cmd 66) must not be misparsed as a status frame
    frag = parse_w5_ble_response({"payload": [{"cmd": 66, "data": base64.b64encode(bytes([1, 2, 3])).decode()}]})
    assert frag == {}


def test_w5_undecodable_returns_empty():
    assert parse_w5_ble_response({"unrelated": "x"}) == {}


def test_a_w5_frame_too_short_to_be_a_status_is_dropped_whole():
    """It used to emit every field whose offset happened to be in range, which
    turned a one-byte ACK into a confident `powerStatus` of 1. The block is 12
    bytes on every firmware, so anything shorter is a broken frame."""
    assert _decode_w5_status(b"\x01") == {"states": {}, "consumables": {}}
    assert _decode_w5_status(bytes(11)) == {"states": {}, "consumables": {}}


def test_the_shortest_real_w5_status_decodes_in_full():
    out = _decode_w5_status(bytes([1, 2, 0, 0, 1, 0, 0, 0, 0x10, 0x20, 55, 1]))
    assert out["states"]["powerStatus"] == 1
    assert out["states"]["mode"] == 2
    assert out["states"]["warningWaterMissing"] == 1
    assert out["states"]["runningStatus"] == 1
    assert out["states"]["pumpRuntime"] == 0x1020
    assert out["consumables"]["filterPercentage"] == 55
    # Nothing beyond byte 11 was sent, so nothing beyond byte 11 is claimed.
    assert "todayPumpRunTime" not in out["states"]
    assert "smartWorkingTime" not in out["states"]


def test_a_longer_w5_status_yields_the_fields_the_longer_firmware_adds():
    data = bytes([1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 90, 1]) + \
        (83460).to_bytes(4, "big") + bytes([15, 25])
    out = _decode_w5_status(data)["states"]
    assert out["todayPumpRunTime"] == 83460
    assert out["smartWorkingTime"] == 15
    assert out["smartSleepTime"] == 25
