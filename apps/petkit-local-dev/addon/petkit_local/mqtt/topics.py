"""Aliyun IoT MQTT topic parser.

Device topics follow the pattern:
  /sys/{pk}/{dn}/thing/event/{event_type}/post       — device publishes events
  /sys/{pk}/{dn}/thing/event/{event_type}/post_reply  — server acknowledges
  /sys/{pk}/{dn}/thing/service/{service_type}         — server sends commands
  /{pk}/{dn}/user/get                                 — server pushes config
  /ota/device/inform/{pk}/{dn}                        — device reports FW version
  /ota/device/upgrade/{pk}/{dn}                       — server sends OTA command

`{pk}` is the product key and `{dn}` the device name — together they are the
device's identity on the broker, and the pair is what `mqtt/bridge.py` looks a
device up by. Parsing and building topics both live here so the two can't
drift: a reply published on a topic the parser wouldn't recognise is a bug the
device sees as silence.

`rewrite_topic` exists for proxy mode: our broker addresses a device by the
credentials we minted, the real Aliyun broker by the ones PetKit issued, so
every frame relayed between them has to change identity on the way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A property post is `thing/event/property/post`, i.e. already covered by
# EVENT_PATTERN with event="property" — there is deliberately no separate
# pattern for it.
EVENT_PATTERN = re.compile(
    r"^/sys/(?P<pk>[^/]+)/(?P<dn>[^/]+)/thing/event/(?P<event>[^/]+)/post$"
)
SERVICE_PATTERN = re.compile(
    r"^/sys/(?P<pk>[^/]+)/(?P<dn>[^/]+)/thing/service/(?P<service>.+)$"
)
USER_GET_PATTERN = re.compile(
    r"^/(?P<pk>[^/]+)/(?P<dn>[^/]+)/user/get$"
)
OTA_INFORM_PATTERN = re.compile(
    r"^/ota/device/inform/(?P<pk>[^/]+)/(?P<dn>[^/]+)$"
)
# Parsed, not just built (see `ota_upgrade_topic`), because proxy mode has to
# recognise an upgrade arriving from the real cloud in order to BLOCK it. An
# unparsed topic would be dropped silently, which looks identical to nothing
# having happened.
OTA_UPGRADE_PATTERN = re.compile(
    r"^/ota/device/upgrade/(?P<pk>[^/]+)/(?P<dn>[^/]+)$"
)


@dataclass
class ParsedTopic:
    """One device topic broken into the parts a handler dispatches on.

    `category` is the coarse kind ("event", "service", "user_get", "ota") and
    `detail` its subject: the event_type for an event, the service name for a
    service, and a fixed word for the categories that have no variable part.
    """

    product_key: str
    device_name: str
    category: str
    detail: str


#: The categories the SERVER publishes on, from the direction table at the top
#: of this module. `None` as the detail means the whole category.
_SERVER_PUBLISHED = (("service", None), ("user_get", None), ("ota", "upgrade"))


def is_server_published(parsed: ParsedTopic) -> bool:
    """Whether this topic is one WE publish on rather than the device.

    The bridge subscribes to `#`, so every frame it sends comes straight back to
    it. Told nothing, it treats its own commands as device traffic: counting
    them, refreshing the device's liveness with them, and naming the device as
    their sender in the panel log.

    Takes an already-parsed topic because every caller has one. `post_reply`
    echoes and `$SYS` chatter never reach here at all — `parse_topic` does not
    recognise them, and the bridge drops what it cannot parse.

    `mqtt/upstream.py::_is_from_device` is the complementary question — what may
    be RELAYED to the real cloud — and stays separate because it answers False
    for an unparseable topic, where the negation of this would answer True.
    """
    # A `_reply` always travels the opposite way to the topic it answers: the
    # DEVICE acknowledges a `thing/service/{name}` on `thing/service/{name}_reply`.
    # Matching those as ours dropped the one frame that proves a command
    # arrived, so a command that vanished and a command that was obeyed looked
    # exactly alike in the log. (`thing/event/{t}/post_reply` — the reply we
    # send — never reaches here at all: EVENT_PATTERN anchors on `/post`, so
    # `parse_topic` does not recognise it.)
    if parsed.detail.endswith("_reply"):
        return False
    return any(parsed.category == cat and (detail is None or parsed.detail == detail)
               for cat, detail in _SERVER_PUBLISHED)


def downstream_filters(pk: str, dn: str) -> list[str]:
    """Everything the server may send ONE device, as subscribable filters.

    A device is subscribed to these on its behalf when it connects
    (`mqtt/auth.py::_server_subscribe`), because the T5 sends no SUBSCRIBE of
    its own: confirmed on hardware, where a session that had authenticated,
    PINGREQ'd and published telemetry for minutes still had an empty
    subscription list. Publishing to a filter nobody holds raises nothing, so
    without this every command is accepted by the broker and then dropped.

    `/ota/device/upgrade/…` is deliberately absent. `ota_upgrade_topic` explains
    why nothing publishes there; leaving the device unsubscribed means even a
    mistake could not reach it with firmware.

    `mqtt/upstream.py::_downstream_topics` is the same question asked of the
    REAL cloud, and does include the OTA topic — subscribing is how an attempt
    from upstream gets seen and blocked rather than silently never arriving.
    """
    return [
        service_topic(pk, dn, "#"),
        event_reply_topic(pk, dn, "+"),
        user_get_topic(pk, dn),
    ]


def parse_topic(topic: str) -> ParsedTopic | None:
    """Parse a device topic, or None if it is not one we recognise.

    None is the normal case, not an error: the bridge subscribes to `#` and so
    sees the broker's own `$SYS` traffic and post_reply echoes too.
    """
    m = EVENT_PATTERN.match(topic)
    if m:
        return ParsedTopic(
            product_key=m.group("pk"),
            device_name=m.group("dn"),
            category="event",
            detail=m.group("event"),
        )

    m = SERVICE_PATTERN.match(topic)
    if m:
        return ParsedTopic(
            product_key=m.group("pk"),
            device_name=m.group("dn"),
            category="service",
            detail=m.group("service"),
        )

    m = USER_GET_PATTERN.match(topic)
    if m:
        return ParsedTopic(
            product_key=m.group("pk"),
            device_name=m.group("dn"),
            category="user_get",
            detail="get",
        )

    m = OTA_INFORM_PATTERN.match(topic)
    if m:
        return ParsedTopic(
            product_key=m.group("pk"),
            device_name=m.group("dn"),
            category="ota",
            detail="inform",
        )

    m = OTA_UPGRADE_PATTERN.match(topic)
    if m:
        return ParsedTopic(
            product_key=m.group("pk"),
            device_name=m.group("dn"),
            category="ota",
            detail="upgrade",
        )

    return None


def event_reply_topic(pk: str, dn: str, event_type: str) -> str:
    """Topic the server acknowledges an event post on."""
    return f"/sys/{pk}/{dn}/thing/event/{event_type}/post_reply"


def service_topic(pk: str, dn: str, service_type: str) -> str:
    """Topic the server sends a command on (start, end, property/set, ...)."""
    return f"/sys/{pk}/{dn}/thing/service/{service_type}"


def user_get_topic(pk: str, dn: str) -> str:
    """Topic the server pushes a requested config resource on."""
    return f"/{pk}/{dn}/user/get"


def ota_upgrade_topic(pk: str, dn: str) -> str:
    """Topic that would trigger a firmware upgrade.

    Nothing publishes here — this add-on exists to keep devices off the cloud,
    and pushing firmware to one is not something it should do by accident. It is
    however SUBSCRIBED to upstream by `mqtt/upstream.py`, precisely so a frame
    the real cloud sends on it can be blocked and recorded rather than relayed.
    """
    return f"/ota/device/upgrade/{pk}/{dn}"


def event_post_topic(pk: str, dn: str, event_type: str) -> str:
    """Topic a device publishes an event on."""
    return f"/sys/{pk}/{dn}/thing/event/{event_type}/post"


def ota_inform_topic(pk: str, dn: str) -> str:
    """Topic a device reports its firmware version on."""
    return f"/ota/device/inform/{pk}/{dn}"


def rewrite_topic(topic: str, to_pk: str, to_dn: str) -> str | None:
    """Re-address a topic to a different product key / device name.

    Proxy mode's MQTT bridge needs this in both directions: our broker addresses
    a device by the credentials WE minted, while the real Aliyun broker uses the
    ones PetKit issued, so a frame relayed either way carries the wrong identity
    until it is rewritten.

    Built on `parse_topic` plus the builders above rather than on a string
    substitution, so a rewrite can only ever produce a topic the parser
    recognises — a subtly malformed one would reach the device as silence.

    Returns:
        The re-addressed topic, or None for a topic outside the known map
        (`post_reply` included — nothing needs to relay an acknowledgement).
    """
    parsed = parse_topic(topic)
    if parsed is None:
        return None
    if parsed.category == "event":
        return event_post_topic(to_pk, to_dn, parsed.detail)
    if parsed.category == "service":
        return service_topic(to_pk, to_dn, parsed.detail)
    if parsed.category == "user_get":
        return user_get_topic(to_pk, to_dn)
    if parsed.category == "ota":
        if parsed.detail == "inform":
            return ota_inform_topic(to_pk, to_dn)
        return ota_upgrade_topic(to_pk, to_dn)
    return None
