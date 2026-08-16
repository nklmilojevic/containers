"""Two-way talk (intercom) audio sink patcher.

PetKit's app talks to the litter box over Agora RTC; the camera patcher replaces
Agora with tserver (local video, one-way), which is why talkback stops working
once you leave the cloud. Listening still works — the device mic rides the
tserver FLV as 16 kHz AAC — so the only missing half is getting the panel's
microphone TO the device speaker.

This patcher adds that half without any new on-device binary. A tiny stock-shell
sink script (`common.TALK_SINK_SCRIPT`) is installed ONCE into the patch store
at apply time, and the pre-init block (`common.PRE_INIT_BLOCKS["talk"]`) starts a
small `nc` listener on TCP `TALK_TCP_PORT` that runs it per connection: the script
feeds a named pipe to the firmware's own `pktool play_aac`, which hands the path
to `media`; media stream-reads the pipe and plays it on the speaker. The add-on's
`/api/devices/{id}/talk` WebSocket transcodes the browser microphone to 16 kHz
mono ADTS-AAC and streams it in.

It is a pre-init block rather than a bind-mount because it starts a process
rather than replacing a binary — the same shape as the SSH patcher. Starting it
before stock init is safe even though it ends up driving pktool: the listener
only needs to be LISTENING at boot, and the speaker path is touched lazily, per
connection, by which time stock init has long since started media.

The only file of ours is the sink script, installed once into the store (like
dropbear, not regenerated at boot); it is listed in `files` so removal deletes it
and rewrites the wrapper without the block. Half-duplex by design: mute listening
while talking, because the device's native echo cancellation lived on the Agora
path this replaces.

Note the listener is unauthenticated and bound to all interfaces (busybox `nc`
has no auth and no per-address bind), so anyone on the LAN can push audio to the
speaker while the patch is applied. It plays audio only — the socket is copied
verbatim into pktool, never a shell — but it is a deliberate LAN exposure, called
out in the patcher description so it is an informed choice.
"""
from __future__ import annotations

from petkit_local.patchers.common import TALK_SINK_NAME, TALK_TCP_PORT

PATCHER_INFO = {
    "id": "talk",
    "name": "Two-Way Talk (Intercom)",
    "description": (
        "Adds a local audio sink so the panel can talk to the device speaker — "
        "real two-way audio without PetKit's cloud, which used Agora (replaced "
        "by Local Camera Streaming).\n\n"
        f"What it does: installs a small sink script once into writable app "
        f"storage and adds a pre-init block to the boot wrapper that runs an nc "
        f"listener on TCP {TALK_TCP_PORT}. Per connection it feeds a named pipe "
        "to the firmware's own pktool play_aac, which hands it to media (the IMP "
        "pipeline that owns the speaker). The add-on transcodes your browser "
        "microphone to 16 kHz mono AAC and streams it in; listening is the "
        "existing camera audio the other way.\n\n"
        "Requires a camera model with a speaker, and is normally used together "
        "with Local Camera Streaming. Removing the patch deletes the sink script "
        "and rewrites the boot wrapper, restoring stock on the next reboot.\n\n"
        f"Note: the listener on TCP {TALK_TCP_PORT} is unauthenticated and reachable "
        "from your whole LAN while applied — it plays audio only (no shell), but "
        "anyone on the network can push sound to the speaker. Apply on trusted "
        "networks only.\n\n"
        "Half-duplex: mute listening while you talk to avoid echo (the device's "
        "native echo cancellation lived on the Agora path this replaces)."
    ),
    # One file of ours: the sink script, installed once into the store (cf.
    # dropbear). Listed here so removal deletes it (patcher_device_files).
    "files": [TALK_SINK_NAME],
    # No architecture: the sink is a stock-shell script; nc + pktool are already
    # on the device, so nothing arch-specific is shipped.
    "arch": None,
    # The sink script is tiny, but every patcher also rewrites /system/app_init.sh
    # — so the floor is the wrapper plus margin, not the script's own size.
    "needs_bytes": 131072,
}
