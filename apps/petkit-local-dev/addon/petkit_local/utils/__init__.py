"""Shared, dependency-free helpers that every other package may import.

The rule that keeps this from becoming a junk drawer: a module belongs here only
if it is about UNTRUSTED INPUT or the FILESYSTEM and knows nothing about PetKit
itself — `coerce` (scalars that must never raise), `dicts` (traversal of device
JSON), `paths` (containment-checked joins), `jsonio` (crash-safe persistence),
`capture` (raw payload logging), `logtext` (bounded, never-raising rendering of
a payload into a log line). `const` is the one deliberate exception: the
device codename tables are pure data with no behaviour, and keeping them here is
what lets `devices`, `ha`, `http` and `media` all answer "what kind of device is
this?" without importing one another.

Nothing here imports from another `petkit_local` package, so any module can
depend on these without risking an import cycle.
"""
