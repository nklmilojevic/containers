import json

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.ha.publisher import HAPublisher
from tests._fakes import FakeMqttClient


def _setup():
    reg = DeviceRegistry()
    dev = reg.get_or_create(petkit_id=1, device_type="t5", serial_number="SN")
    pub = HAPublisher(reg, {})
    pub._client = FakeMqttClient()
    pub._connected = True
    return reg, dev, pub


async def test_publish_pet_discovery_uses_distinct_identifiers_and_topics():
    reg, dev, pub = _setup()
    pet = {"id": 1, "name": "Mruczek"}  # same numeric id as device id=1 on purpose

    await pub.publish_pet_discovery(pet)

    configs = [json.loads(p) for t, p, _ in pub._client.published if t.endswith("/config")]
    assert configs, "expected at least one discovery config"
    for cfg in configs:
        assert cfg["device"]["identifiers"] == ["petkit_pet_1"]
        assert cfg["state_topic"] == "petkit-local/pet/1/state"
        assert cfg["availability"]["topic"] == "petkit-local/pet/1/availability"
        assert cfg["unique_id"].startswith("petkit_pet_1_")

    avail = [p for t, p, kw in pub._client.published if t == "petkit-local/pet/1/availability"]
    assert avail == ["online"]


async def test_publish_pet_state_builds_stats_and_resolves_device_name(event_store):
    reg, dev, pub = _setup()
    await event_store.upsert_event({"device_id": 1, "event_type": "pet_out",
                                    "event_kind": "toilet_visit", "pet_id": 7, "ts": 1000.0,
                                    "content_json": '{"pet_weight": 2200}'})

    await pub.publish_pet_state({"id": 7, "name": "Mruczek"}, event_store)

    state_msgs = [p for t, p, _ in pub._client.published if t == "petkit-local/pet/7/state"]
    assert state_msgs
    state = json.loads(state_msgs[-1])["state"]
    assert state["lastVisitWeight"] == 2200.0
    assert state["lastDeviceUsed"] == "T5 SN"
    assert state["lastVisit"] is not None


async def test_publish_pet_state_noop_without_client(event_store):
    reg = DeviceRegistry()
    pub = HAPublisher(reg, {})
    await pub.publish_pet_state({"id": 1, "name": "X"}, event_store)  # must not raise
