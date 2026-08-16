"""One module per PetKit endpoint the firmware calls.

Modules are named after the endpoint they answer (`signup.py` ->
`dev_signup`), with two exceptions: `stubs.py` collects the endpoints that need
no state of their own, and `_common.py` holds the request -> device resolution
every handler starts with. `http/server.py` maps URLs onto these handlers;
nothing here registers a route of its own.

Two conventions hold across all of them: a handler never raises on
device-supplied input, and a handler that cannot identify its caller still
answers with a valid, empty-ish body rather than an error status. The single
exception is `signup`, which has nothing to key a registry entry on and says so
with a 400.
"""
