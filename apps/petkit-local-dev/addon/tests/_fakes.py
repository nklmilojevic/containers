"""Stand-ins for the two MQTT objects several test modules need.

Not fixtures: these are instantiated ad hoc, sometimes more than once per test,
and assigned onto a publisher or a bridge. A module here rather than
`conftest.py` because importing from a conftest is a pytest anti-pattern — that
file is for fixtures pytest collects, not for a helper library.
"""
from __future__ import annotations

from typing import Any


class FakeMqttClient:
    """Records every publish instead of sending it.

    Stands in for the aiomqtt client that `HAPublisher` and `MQTTBridge` each
    hold one of. `published` is `(topic, payload, kwargs)` per call, in order.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, Any, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: Any, **kw: Any) -> None:
        self.published.append((topic, payload, kw))


class FakeMessage:
    """One inbound MQTT message, shaped as aiomqtt hands it over."""

    def __init__(self, topic: Any, payload: Any) -> None:
        self.topic = topic
        self.payload = payload
