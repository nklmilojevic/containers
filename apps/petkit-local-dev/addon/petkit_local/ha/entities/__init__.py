"""Entity declarations, one module per HA component type.

Every module here is data only: lists of `EntityDef` (see `ha/discovery.py`)
with no logic, so the several hundred entities across the supported device
types can be reviewed as a table rather than as code. `ha/categories.py`
decides which lists a given device type receives.

Imports are limited to `EntityDef` and the protocol TABLES in `events/codes.py`
— never logic. A table is allowed because the alternative is worse: restating
an enum here would put protocol knowledge outside `events/codes.py`, which is
the one place it is allowed to live, and a second copy is how the two drift.
"""
