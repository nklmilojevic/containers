"""Tests for petkit_local/mqtt/upstream.py — proxy mode's bridge to Aliyun.

Driven by calling the methods directly, the way `tests/test_bridge.py` drives
`_handle_event`: no broker, no network, no aiomqtt client. What that can prove
is the routing — the echo-loop allow-list, the topic rewrite, the OTA block, the
redaction of downstream frames — which is where the bugs would be.

What it CANNOT prove is anything about the real Aliyun endpoint: its port, its
securemode, or whether an account's device is provisioned for the client-id
shape we build. The signature test below is a synthetic-signature tautology,
exactly as the repo already grades the rest of the MQTT side.
"""
import asyncio
import json
import ssl

import pytest

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.http.redact import RedactionPolicy
from petkit_local.mqtt.auth import compute_aliyun_sign, parse_client_id
from petkit_local.mqtt.topics import parse_topic, rewrite_topic
from petkit_local.mqtt.upstream import (
    UpstreamCredentials,
    UpstreamMQTT,
    _is_from_device,
    build_credentials,
    build_tls_context,
)

REAL = {"product_key": "realpk", "device_name": "realdn",
        "device_secret": "realsecret", "mqtt_host": "realpk.iot-as-mqtt.example"}


class _FakeClient:
    """Stands in for the aiomqtt client held per device."""

    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    async def publish(self, topic, payload):
        if self.fail:
            raise RuntimeError("upstream gone")
        self.published.append((topic, payload))


class _FakeMessage:
    def __init__(self, topic, payload, qos=0, retain=False):
        self.topic = topic
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.qos = qos
        self.retain = retain


def _bridge(tmp_path, *, store=None, hub=None, live=None, connected=True):
    registry = DeviceRegistry()
    device = registry.get_or_create(petkit_id=100, device_type="t5", serial_number="SN1")

    creds = UpstreamCredentials(tmp_path / "proxy_upstream.json")
    creds.put(100, REAL)

    local = []

    async def publish_local(topic, payload, qos=0, retain=False):
        # Keeps the kwargs so a relay that started passing them again would be
        # recorded rather than raising a TypeError -- the real sink takes
        # (topic, payload) only, and the defaults here are what it uses.
        local.append((topic, payload, qos, retain))

    def policy(dev):
        return RedactionPolicy(device=dev, api_url="http://192.0.2.199:8080/6/",
                               mqtt_host="192.0.2.199")

    up = UpstreamMQTT(registry, creds, policy, publish_local, hub=hub,
                      event_store=store,
                      live_config=live if live is not None else {})
    client = _FakeClient()
    if connected:
        up._clients_set(100, client)
    return up, device, client, local


# --- the echo-loop allow-list ----------------------------------------------

@pytest.mark.parametrize("topic,relayed", [
    # What a device originates.
    ("/sys/pk/dn/thing/event/property/post", True),
    ("/sys/pk/dn/thing/event/pet_out/post", True),
    ("/ota/device/inform/pk/dn", True),
    # What WE publish. The bridge holds one `#` subscription, so it sees its own
    # downward frames come back — relaying these would loop forever.
    ("/sys/pk/dn/thing/event/property/post_reply", False),
    ("/sys/pk/dn/thing/service/property/set", False),
    ("/pk/dn/user/get", False),
    ("$SYS/broker/uptime", False),
    ("/ota/device/upgrade/pk/dn", False),
])
def test_only_device_originated_frames_go_upstream(topic, relayed):
    assert _is_from_device(topic) is relayed


async def test_forward_up_rewrites_the_topic_to_the_real_identity(tmp_path):
    """Our broker addresses a device by the credentials we minted; Aliyun uses
    the ones PetKit issued."""
    up, device, client, _ = _bridge(tmp_path)
    topic = f"/sys/{device.mqtt_product_key}/{device.mqtt_device_name}/thing/event/property/post"

    assert await up.forward_up(device, topic, {"params": {"x": 1}}) is True

    sent_topic, payload = client.published[0]
    assert sent_topic == "/sys/realpk/realdn/thing/event/property/post"
    assert json.loads(payload)["params"]["x"] == 1
    # The rewrite produced something the parser still recognises.
    assert parse_topic(sent_topic).product_key == "realpk"


async def test_forward_up_declines_our_own_echo(tmp_path):
    up, device, client, _ = _bridge(tmp_path)
    reply = (f"/sys/{device.mqtt_product_key}/{device.mqtt_device_name}"
             "/thing/event/property/post_reply")

    assert await up.forward_up(device, reply, {"code": 200}) is False
    assert client.published == []


async def test_forward_up_is_silent_when_not_connected(tmp_path):
    up, device, _, _ = _bridge(tmp_path, connected=False)
    topic = f"/sys/{device.mqtt_product_key}/{device.mqtt_device_name}/thing/event/x/post"
    assert await up.forward_up(device, topic, {}) is False


async def test_forward_up_swallows_an_upstream_failure(tmp_path):
    """The local delivery has already happened; an upstream problem must not
    surface as an exception in the bridge's message loop."""
    up, device, _, _ = _bridge(tmp_path)
    up._clients_set(100, _FakeClient(fail=True))
    topic = f"/sys/{device.mqtt_product_key}/{device.mqtt_device_name}/thing/event/x/post"

    assert await up.forward_up(device, topic, {}) is False


# --- downstream: what the cloud sends -------------------------------------

async def test_a_downstream_command_is_readdressed_to_us(tmp_path):
    up, device, _, local = _bridge(tmp_path)
    msg = _FakeMessage("/sys/realpk/realdn/thing/service/property/set",
                       {"params": {"autoWork": 1}})

    await up._on_upstream(device, REAL, msg)

    topic, payload, _qos, _retain = local[0]
    assert topic == (f"/sys/{device.mqtt_product_key}/{device.mqtt_device_name}"
                     "/thing/service/property/set")
    assert json.loads(payload)["params"]["autoWork"] == 1


async def test_a_downstream_run_cmd_is_redacted(tmp_path, event_store):
    up, device, _, local = _bridge(tmp_path, store=event_store)
    msg = _FakeMessage("/sys/realpk/realdn/thing/service/property/set",
                       {"params": {"content": json.dumps({"user_cmd": {"run_cmd": "id"}})}})

    await up._on_upstream(device, REAL, msg)

    assert local and b"run_cmd" not in local[0][1]
    rows = await event_store.recent_blocked_attempts()
    assert [r["kind"] for r in rows] == ["rce"]
    assert rows[0]["transport"] == "mqtt"


async def test_a_relayed_frame_is_never_retained_or_raised_above_qos0(tmp_path):
    """The cloud's qos/retain are deliberately NOT carried over.

    A retained `thing/service/start` would sit on our broker and be
    redelivered on every reconnect, scooping the box each time. Raising QoS
    cannot help either: delivery is min(publish, subscription) and the
    server-side subscription is QoS 0, while making it QoS 1 drops the T5's
    session outright. So a cloud frame published at qos=1/retain=True must
    still reach our broker as a plain qos-0, non-retained publish."""
    up, device, _, local = _bridge(tmp_path)
    msg = _FakeMessage("/sys/realpk/realdn/thing/service/start",
                       {"params": {"start_action": 0}}, qos=1, retain=True)

    await up._on_upstream(device, REAL, msg)

    _topic, _payload, qos, retain = local[0]
    assert (qos, retain) == (0, False)


async def test_a_post_reply_from_the_cloud_is_not_relayed(tmp_path):
    """Our own broker already acknowledges every event the device posts;
    relaying the cloud's ack too would double it."""
    up, device, _, local = _bridge(tmp_path)
    msg = _FakeMessage("/sys/realpk/realdn/thing/event/property/post_reply", {"code": 200})

    await up._on_upstream(device, REAL, msg)
    assert local == []


@pytest.mark.parametrize("topic", [
    "/sys/realpk/realdn/thing/event/property/post",
    "/sys/realpk/realdn/thing/event/pet_out/post",
    "/ota/device/inform/realpk/realdn",
])
async def test_a_device_direction_frame_from_upstream_is_not_relayed(tmp_path, topic):
    """The loop from #20: relayed down, it comes back through the bridge's `#`
    subscription, is ingested as device traffic, and goes straight back up."""
    up, device, _, local = _bridge(tmp_path)

    await up._on_upstream(device, REAL, _FakeMessage(topic, {"id": "495", "params": {}}))
    assert local == []


# --- the one thing that is never relayed ------------------------------------

async def test_an_ota_push_is_blocked_and_recorded(tmp_path, event_store):
    up, device, _, local = _bridge(tmp_path, store=event_store)
    msg = _FakeMessage("/ota/device/upgrade/realpk/realdn",
                       {"data": {"url": "http://petkt.com/fw.bin", "md5": "abc"}})

    await up._on_upstream(device, REAL, msg)

    # Nothing reached the device.
    assert local == []
    rows = await event_store.recent_blocked_attempts()
    assert [r["kind"] for r in rows] == ["ota"]
    assert rows[0]["endpoint"] == "/ota/device/upgrade/realpk/realdn"
    assert "fw.bin" in rows[0]["payload_json"]


def test_the_upgrade_topic_now_parses():
    """It used to return None, so an upgrade frame was dropped SILENTLY —
    indistinguishable from nothing having happened."""
    parsed = parse_topic("/ota/device/upgrade/pk/dn")
    assert parsed is not None
    assert (parsed.category, parsed.detail) == ("ota", "upgrade")


@pytest.mark.parametrize("topic", [
    "/sys/pk/dn/thing/event/property/post",
    "/sys/pk/dn/thing/service/property/set",
    "/pk/dn/user/get",
    "/ota/device/inform/pk/dn",
    "/ota/device/upgrade/pk/dn",
])
def test_rewrite_round_trips_through_the_parser(topic):
    """A rewrite that produced a topic the parser did not recognise would reach
    the device as silence."""
    rewritten = rewrite_topic(topic, "PK2", "DN2")
    parsed = parse_topic(rewritten)
    assert parsed.product_key == "PK2" and parsed.device_name == "DN2"
    assert (parsed.category, parsed.detail) == (parse_topic(topic).category,
                                                parse_topic(topic).detail)


def test_rewrite_declines_a_topic_outside_the_map():
    assert rewrite_topic("/sys/pk/dn/thing/event/x/post_reply", "a", "b") is None
    assert rewrite_topic("$SYS/broker/uptime", "a", "b") is None


# --- credentials ------------------------------------------------------------

def test_the_upstream_tls_context_does_not_verify_the_cloud():
    """Aliyun IoT chains to a private root, so a verifying context never connects.

    Asserted rather than left to the default because the default is what BROKE
    it: `TLSParameters()` means "use the system trust store", and no system
    store contains `CN=Aliyun IoT Root CA` — every connection died with
    CERTIFICATE_VERIFY_FAILED before a frame was ever exchanged.
    """
    ctx = build_tls_context()
    assert ctx.verify_mode == ssl.CERT_NONE
    # Both halves matter: leaving hostname checking on with CERT_NONE is not
    # merely inconsistent, `SSLContext` refuses the combination outright.
    assert ctx.check_hostname is False


def test_credentials_persist_and_reload(tmp_path):
    path = tmp_path / "proxy_upstream.json"
    UpstreamCredentials(path).put(100, REAL)

    reloaded = UpstreamCredentials(path)
    assert reloaded.get(100)["device_secret"] == "realsecret"
    assert reloaded.get(999) is None


def test_credentials_missing_file_is_not_an_error(tmp_path):
    assert UpstreamCredentials(tmp_path / "nope.json").all() == {}


def test_the_connect_password_matches_what_our_own_broker_verifies():
    """Same function used in the opposite direction, so the two cannot disagree
    about what the signature covers (the BARE `{pk}.{dn}`).

    NOTE: this is a synthetic-signature tautology. It proves we build what
    `mqtt/auth.py` would accept; it proves nothing about what Aliyun accepts.
    That needs hardware.
    """
    conn = build_credentials(REAL, timestamp="1700000000000")

    parsed = parse_client_id(conn["client_id"])
    assert parsed["product_key"] == "realpk" and parsed["device_name"] == "realdn"
    assert parsed["params"]["securemode"] == "2"
    assert conn["username"] == "realdn&realpk"
    assert conn["password"] == compute_aliyun_sign(
        "realsecret", "realpk.realdn", "realdn", "realpk", "1700000000000")


# --- lifecycle --------------------------------------------------------------

async def test_nothing_runs_while_the_config_says_no(tmp_path):
    up, _, _, _ = _bridge(tmp_path, live={"proxy_mode": False}, connected=False)
    assert up.wanted() is False
    await up.start()
    assert up.running is False


async def test_the_bridge_is_wanted_only_with_both_switches_on(tmp_path):
    up, _, _, _ = _bridge(tmp_path, live={"proxy_mode": True, "proxy_mqtt_bridge": True},
                          connected=False)
    assert up.wanted() is True
    up._live_config["proxy_mqtt_bridge"] = False
    assert up.wanted() is False
    up._live_config.update({"proxy_mqtt_bridge": True, "proxy_mode": False})
    assert up.wanted() is False


async def test_stop_is_idempotent(tmp_path):
    up, _, _, _ = _bridge(tmp_path, connected=False)
    await up.stop()
    await up.stop()
    assert up.running is False


async def test_a_device_with_no_credentials_is_never_connected(tmp_path):
    registry = DeviceRegistry()
    registry.get_or_create(petkit_id=100, device_type="t5", serial_number="SN1")
    up = UpstreamMQTT(registry, UpstreamCredentials(tmp_path / "none.json"),
                      lambda d: RedactionPolicy(device=d), lambda t, p: None,
                      live_config={"proxy_mode": True, "proxy_mqtt_bridge": True})
    await up.start()
    assert up.running is False


async def test_reconcile_reaps_a_finished_task_so_the_device_reconnects(tmp_path):
    """`_run` RETURNS rather than looping in several cases. A done task left in
    the dict makes every later pass skip that device — its bridge dead until
    proxy mode is toggled off and on."""
    up, device, _, _ = _bridge(tmp_path,
                               live={"proxy_mode": True, "proxy_mqtt_bridge": True},
                               connected=False)

    async def _returns_immediately():
        return

    done = asyncio.get_running_loop().create_task(_returns_immediately())
    await asyncio.sleep(0)
    up._tasks[100] = done
    assert done.done()

    await up.reconcile()
    assert up._tasks.get(100) is not done


async def test_stop_cancels_every_connection(tmp_path):
    up, _, _, _ = _bridge(tmp_path,
                          live={"proxy_mode": True, "proxy_mqtt_bridge": True},
                          connected=False)

    async def _forever():
        await asyncio.sleep(3600)

    task = asyncio.get_running_loop().create_task(_forever())
    up._tasks[100] = task

    await up.stop()
    assert task.cancelled()
    assert up.running is False
