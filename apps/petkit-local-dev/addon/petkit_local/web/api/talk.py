"""Two-way talk: stream the browser microphone to the device speaker.

The `talk` patcher installs a TCP audio sink on the device (see
`patchers/talk.py`); this WebSocket is the other end of it. Kept out of
`panel.py` because it owns an ffmpeg subprocess per session and its own small
message protocol.
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import aiohttp
from aiohttp import web

from petkit_local.patchers.common import TALK_TCP_PORT


async def api_talk(request: web.Request) -> web.WebSocketResponse:
    """Two-way talk: stream the browser microphone to the device speaker.

    The frontend's MediaRecorder sends webm/opus over this WebSocket in ~250 ms
    slices (a `talk_start` text frame, then binary, then `talk_stop`). One
    ffmpeg per session transcodes the stream to 16 kHz mono ADTS-AAC — the
    device speaker's format — and connects straight to the device's talk sink
    (`tcp://<ip>:TALK_TCP_PORT`, installed by the `talk` patcher), which pipes it
    to `pktool play_aac` and thence to `media`. ffmpeg does both the transcode
    and the TCP connection, so nothing here touches a socket directly.

    Half-duplex by design: the panel mutes listening while talking, because the
    echo cancellation that made full duplex safe lived on the Agora path the
    camera patcher replaced. Needs the device IP (from a state report) and the
    `talk` patcher applied; without the sink listening, ffmpeg's TCP connect
    fails and it exits at once, so the session does not hang — but the audio is
    dropped silently (ffmpeg's stderr is suppressed and its early exit is not
    surfaced to the client yet).
    """
    reg = request.app["registry"]
    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024, heartbeat=25)
    await ws.prepare(request)

    try:
        did = int(request.match_info["id"])
    except ValueError:
        await ws.send_json({"type": "error", "msg": "bad device id"})
        await ws.close()
        return ws
    d = reg.get(did)
    device_ip = d.state.get("ip", "") if d else ""
    if not d or not device_ip:
        await ws.send_json({"type": "error",
                            "msg": "device IP unknown — wait for a state report"})
        await ws.close()
        return ws

    ffmpeg: asyncio.subprocess.Process | None = None

    async def start() -> None:
        nonlocal ffmpeg
        if ffmpeg is not None:
            return
        # Low-latency decode: the input is a live webm/opus stream, so keep
        # ffmpeg from buffering ahead before it starts emitting. Output goes
        # straight to the device sink over TCP.
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0",
            "-i", "pipe:0",
            "-ar", "16000", "-ac", "1", "-c:a", "aac", "-b:a", "48k",
            "-f", "adts", f"tcp://{device_ip}:{TALK_TCP_PORT}",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def stop() -> None:
        nonlocal ffmpeg
        if ffmpeg is None:
            return
        proc, ffmpeg = ffmpeg, None
        # Close the input so ffmpeg flushes its trailer and the device sink sees
        # EOF (ending that play session); kill only if it will not exit.
        if proc.stdin and not proc.stdin.is_closing():
            with contextlib.suppress(Exception):
                proc.stdin.write_eof()
        try:
            await asyncio.wait_for(proc.wait(), timeout=6)
        except (asyncio.TimeoutError, ProcessLookupError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                if ffmpeg is None:
                    await start()
                if ffmpeg and ffmpeg.stdin and not ffmpeg.stdin.is_closing():
                    ffmpeg.stdin.write(msg.data)
                    # Apply backpressure: if the device sink stalls, ffmpeg stops
                    # reading stdin and this awaits rather than buffering the
                    # browser's stream without bound.
                    await ffmpeg.stdin.drain()
            elif msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    evt = json.loads(msg.data)
                except ValueError:
                    continue
                kind = evt.get("type")
                if kind == "talk_start":
                    await start()
                    if not ws.closed:
                        await ws.send_json({"type": "talking"})
                elif kind == "talk_stop":
                    await stop()
                    if not ws.closed:
                        await ws.send_json({"type": "stopped"})
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
    except (asyncio.CancelledError, OSError, RuntimeError):
        # OSError covers the transport failing under us: ConnectionReset and
        # BrokenPipe (writing to an ffmpeg that has exited), and a missing ffmpeg
        # binary — all handled the same way, with `stop()` in the finally.
        pass
    finally:
        await stop()
    return ws
