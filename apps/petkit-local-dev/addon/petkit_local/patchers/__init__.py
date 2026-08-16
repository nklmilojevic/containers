"""Auto-patchers for PetKit device firmware.

Each patcher modifies the device filesystem via run_cmd (heartbeat or MQTT),
with all binary patching done server-side in Python. The device only transfers
original files and receives pre-validated patched versions.

Firmware modification carries a risk of bricking and may void warranty.
"""
