"""HAPublisher command-loop resilience.

Reconnecting the HA client costs a full rediscovery of every entity of every
device, so a single unusable command payload must never reach the reconnect
handler.
"""
import asyncio
import sys
import types

from petkit_local.devices.registry import DeviceRegistry
from petkit_local.ha.publisher import HAPublisher
from tests._fakes import FakeMessage, FakeMqttClient


async def _stream(*messages):
    for message in messages:
        yield message


async def _setup():
    """A publisher with a real, discovered device so commands route for real."""
    reg = DeviceRegistry()
    dev = reg.get_or_create(petkit_id=1, device_type="t5", serial_number="SN")
    pub = HAPublisher(reg, {})
    pub._client = FakeMqttClient()
    pub._connected = True
    await pub.publish_discovery(dev)  # builds the command-routing index
    return reg, dev, pub


def _switch_entity(pub, device_id: int = 1):
    index = pub._commands._entity_index[device_id]
    return next((suffix, entity) for suffix, entity in index.items()
                if entity.component == "switch" and entity.value_path.startswith("settings."))


async def test_unusable_command_payload_does_not_stop_the_command_loop():
    reg, dev, pub = await _setup()
    suffix, entity = _switch_entity(pub)
    field = entity.value_path.split(".")[-1]
    dev.config.setdefault("settings", {})[field] = 0

    await pub._commands.consume_commands(_stream(
        FakeMessage(f"petkit-local/1/cmd/{suffix}", b"\xff\xfe\x00"),  # not UTF-8
        FakeMessage(f"petkit-local/1/cmd/{suffix}", b"ON"),
    ))

    # The command behind the bad one was still applied.
    assert dev.config["settings"][field] == 1


async def test_command_handler_failure_does_not_stop_the_command_loop():
    reg, dev, pub = await _setup()
    seen = []

    async def flaky(message):
        seen.append(message.topic)
        if len(seen) == 1:
            raise RuntimeError("boom inside a command handler")

    pub._commands.handle_command = flaky
    await pub._commands.consume_commands(_stream(
        FakeMessage("petkit-local/1/cmd/a", b"ON"),
        FakeMessage("petkit-local/1/cmd/b", b"OFF"),
    ))

    assert seen == ["petkit-local/1/cmd/a", "petkit-local/1/cmd/b"]


async def test_connection_error_propagates_out_of_the_command_loop():
    """A lost connection must still reach start()'s reconnect handler."""
    class ConnectionLost(Exception):
        pass

    reg, dev, pub = await _setup()

    async def dead(message):
        raise ConnectionLost("broker went away")

    pub._commands.handle_command = dead
    try:
        await pub._commands.consume_commands(_stream(FakeMessage("petkit-local/1/cmd/a", b"ON")),
                                    (ConnectionLost,))
    except ConnectionLost:
        pass
    else:
        assert False, "connection errors must not be swallowed"


async def test_cancellation_propagates_out_of_the_command_loop():
    reg, dev, pub = await _setup()

    async def cancelled(message):
        raise asyncio.CancelledError()

    pub._commands.handle_command = cancelled
    try:
        await pub._commands.consume_commands(_stream(FakeMessage("petkit-local/1/cmd/a", b"ON")))
    except asyncio.CancelledError:
        pass
    else:
        assert False, "shutdown must not be swallowed"


async def test_non_numeric_device_id_in_topic_is_ignored():
    reg, dev, pub = await _setup()
    before = len(pub._client.published)
    await pub._commands.consume_commands(_stream(FakeMessage("petkit-local/not-an-id/cmd/auto_work", b"ON")))
    assert len(pub._client.published) == before


async def test_start_keeps_one_subscription_across_a_bad_command():
    """End-to-end over start(): a bad command must not cost a rediscovery."""
    reg, dev, pub = await _setup()
    pub._host = "ha-broker"
    suffix, entity = _switch_entity(pub)
    field = entity.value_path.split(".")[-1]
    dev.config.setdefault("settings", {})[field] = 0

    queue = [
        FakeMessage(f"petkit-local/1/cmd/{suffix}", b"\xff\xfe\x00"),
        FakeMessage(f"petkit-local/1/cmd/{suffix}", b"ON"),
    ]
    drained = asyncio.Event()
    connects = []
    subscriptions = []

    class FakeClient:
        def __init__(self, **kwargs):
            connects.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def subscribe(self, topic):
            subscriptions.append(topic)

        async def publish(self, topic, payload, **kw):
            pass

        @property
        def messages(self):
            return self._messages()

        async def _messages(self):
            for message in queue:
                yield message
            drained.set()
            await asyncio.Event().wait()  # a live session just waits for more

    fake_aiomqtt = types.ModuleType("aiomqtt")
    fake_aiomqtt.Client = FakeClient
    fake_aiomqtt.MqttError = type("MqttError", (Exception,), {})

    real_aiomqtt = sys.modules.get("aiomqtt")
    sys.modules["aiomqtt"] = fake_aiomqtt
    try:
        task = asyncio.create_task(pub.start())
        await asyncio.wait_for(drained.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if real_aiomqtt is None:
            sys.modules.pop("aiomqtt", None)
        else:
            sys.modules["aiomqtt"] = real_aiomqtt

    assert len(connects) == 1, "the bad command forced a reconnect"
    assert subscriptions == ["petkit-local/+/cmd/+"], "the bad command dropped the subscription"
    assert dev.config["settings"][field] == 1
