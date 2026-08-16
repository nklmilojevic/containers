"""The device-facing MQTT half of the add-on: broker, auth, topics and bridge.

The physical device speaks Aliyun IoT MQTT, so the add-on embeds a broker
(`broker.py`) that accepts its credentials (`auth.py`) on the topic layout it
expects (`topics.py`). `bridge.py` is a client of that embedded broker and the
seam to Home Assistant — it is the only module here that knows HA exists.

Not to be confused with Home Assistant's own broker (usually Mosquitto), which
`ha/publisher.py` owns; the two connections are never the same.
"""
