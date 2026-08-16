"""What happened, and when: the persistent event/media/pet history.

Events reach the add-on over two transports - `dev_event_report` over HTTP and
`thing/event/*` over MQTT - which is exactly why `ingest.py` exists: both are
normalised by the same functions so behaviour cannot drift by transport, and
its module docstring carries the capture-confirmed protocol notes (the two
event-code namespaces, the numeric event_type table, the moduleType mapping).
`store.py` is the SQLite persistence behind that, and `models.py` the schema.

Sessions (a visit plus its sub-events) are NOT stored pre-grouped: they are
built at query time by `ingest.group_sessions`, keeping the grouping heuristic
in readable Python rather than in SQL.
"""
