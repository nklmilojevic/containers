"""Applying and removing the on-device binary patches.

The endpoints are thin; the work is `_patcher_apply` / `_patcher_remove`, which
run detached from the request because a run takes minutes (it waits out device
reboots) and narrate every step onto the hub, since from the UI's side this is
a multi-minute silent operation otherwise.

Both compose rather than overwrite: the final step rewrites the device's
`app_init.sh` wrapper from the FULL active set, so removing one patcher leaves
the others working.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from aiohttp import web

from petkit_local.patchers.cacert import (
    PATCHER_INFO as CACERT_PATCHER, load_our_cert, patch_ca_bundle,
)
from petkit_local.patchers.camera import PATCHER_INFO as CAMERA_PATCHER
from petkit_local.patchers.cloud import PATCHER_INFO as CLOUD_PATCHER, patch_cloud
from petkit_local.patchers.common import (
    DEVICE_HTTPD_PORT, TALK_SINK_NAME, TALK_SINK_SCRIPT, app_init_wrapper_path,
    build_wrapper_remove_cmd, cleanup_staged, detect_patch_storage_dir,
    download_from_device, ensure_space_for, generate_app_init_wrapper, md5hex,
    patched_file_path, patcher_device_files, send_run_cmd, stage_file,
    wait_for_heartbeat,
)
from petkit_local.patchers.mqtt import PATCHER_INFO as MQTT_PATCHER, patch_ctrl
from petkit_local.patchers.ssh import (
    AUTHKEYS_STAGED_NAME,
    PATCHER_INFO as SSH_PATCHER, build_install_commands as ssh_install_commands,
    ARCH_TO_BINARY as SSH_ARCH_TO_BINARY,
    DBKEY_RESERVE_BYTES, dropbear_path_for,
)
from petkit_local.patchers.talk import PATCHER_INFO as TALK_PATCHER
from petkit_local.patchers.verify import assert_download_plausible, elf_arch
from petkit_local.web.api._common import _device_or_404, _json_body

if TYPE_CHECKING:
    from petkit_local.devices.base import Device
    from petkit_local.devices.registry import DeviceRegistry
    from petkit_local.web.hub import EventHub

log = logging.getLogger(__name__)


#: One lock per device id, so patcher runs on the same device serialise. They
#: share the device's `killall httpd`, its /system writes and its reboot, none
#: of which tolerate a second run interleaving.
_PATCHER_LOCKS: dict[int, asyncio.Lock] = {}

ALL_PATCHERS: dict[str, dict[str, Any]] = {
    p["id"]: p for p in [MQTT_PATCHER, CLOUD_PATCHER, CACERT_PATCHER, CAMERA_PATCHER, SSH_PATCHER, TALK_PATCHER]
}


async def api_patcher_status(request: web.Request) -> web.Response:
    """Per-patcher status for a device: `{"patchers", "device_ip", "supported"}`.

    Only Ingenic Linux devices can be patched at all; anything else answers
    `supported: False` with no patchers rather than a 400. The MQTT patcher is
    reported as active-and-`greyed` while the device holds an MQTT session,
    because a live session is proof the patch took regardless of what was
    recorded.
    """
    d = _device_or_404(request)
    if not d.is_next_gen:
        return web.json_response({"patchers": {}, "device_ip": "", "supported": False})

    active = _get_active_patchers(d)
    result: dict[str, dict[str, Any]] = {}
    for pid, pinfo in ALL_PATCHERS.items():
        applied = pid in active
        status = "applied" if applied else "not applied"
        if pid == "mqtt" and d.mqtt_connected:
            status = "active (MQTT connected)"
        entry: dict[str, Any] = {
            "id": pid,
            "name": pinfo["name"],
            "description": pinfo["description"],
            "status": status,
            "applied": applied,
            "unavailable": "",
            "greyed": pid == "mqtt" and d.mqtt_connected,
            # What to warn about before we know the model. The actual gate at
            # apply time uses the measured size of the patched file, which is
            # smaller on every model we have measured.
            "needs_bytes": pinfo["needs_bytes"],
        }
        if pinfo.get("needs_pubkey"):
            entry["needs_pubkey"] = True
            entry["ssh_pubkey"] = d.config.get("ssh_pubkey", "")
        result[pid] = entry
    return web.json_response({"patchers": result, "device_ip": d.state.get("ip", ""), "supported": True})


async def api_patcher_apply(request: web.Request) -> web.Response:
    """Start applying or removing a patcher; answers `{ok, patcher, action}`.

    The 200 means "accepted", not "done": a run takes minutes (it waits out
    device reboots), so it is spawned as a tracked background task and reports
    progress as `patcher` events on the hub, which the UI follows over the
    WebSocket. Failures land there too, never in this response.
    """
    reg = request.app["registry"]
    hub = request.app["hub"]
    d = _device_or_404(request)
    did = d.petkit_id

    if not d.is_next_gen:
        return web.json_response(
            {"error": "patchers only supported on Linux devices"}, status=400)

    body = await _json_body(request)

    patcher_id = body.get("patcher")
    action = body.get("action", "apply")
    if patcher_id not in ALL_PATCHERS:
        return web.json_response({"error": f"unknown patcher: {patcher_id}"}, status=400)
    # SSH needs a public key. Accept it in the request, persist it on the device
    # config so a re-apply after an OTA does not ask again, and validate it
    # before spawning a background task that would fail minutes later.
    pubkey = body.get("pubkey", "").strip() or d.config.get("ssh_pubkey", "")
    if patcher_id == "ssh" and action != "remove":
        if not pubkey:
            return web.json_response({"error": "paste your SSH public key"}, status=400)
        if not any(pubkey.startswith(t) for t in ("ssh-rsa ", "ecdsa-sha2-", "ssh-ed25519 ")):
            return web.json_response({"error": "not a recognised SSH public key format"}, status=400)
        d.config["ssh_pubkey"] = pubkey
        reg.save()

    device_ip = d.state.get("ip", "")
    if not device_ip:
        return web.json_response({"error": "device IP not known - wait for a state_report"}, status=400)

    api_url = request.app["cfg"].get("api_url", "")
    if not api_url:
        return web.json_response({"error": "api_url not configured"}, status=500)

    # Extract host:port from api_url for the device to wget from
    parsed = urlparse(api_url)
    download_base = f"http://{parsed.hostname}:{parsed.port or 80}/patcher/download/{did}"

    # Launch as a background task — the UI tracks progress via WS events. A
    # patcher run outlives its request by minutes (it sleeps between device
    # reboots), so it must be pinned and drained at shutdown, not orphaned.
    async def _run() -> None:
        """Drive one patcher run, reporting progress and failure over the hub.

        Cancellation is re-raised after logging it: this task is drained at
        shutdown, and swallowing CancelledError would hang the drain.
        """
        try:
            # One run at a time per device. Two concurrent runs would race over
            # `killall httpd` — which is global and cannot target a port — and
            # each would tear down the other's file server mid-transfer.
            lock = _PATCHER_LOCKS.setdefault(did, asyncio.Lock())
            if lock.locked():
                hub.publish("patcher", did,
                            f"[{patcher_id}] waiting for the running patcher to finish...")
            async with lock:
                if action == "remove":
                    await _patcher_remove(d, patcher_id, device_ip, download_base,
                                          hub, request.app)
                else:
                    await _patcher_apply(d, patcher_id, device_ip, download_base, hub, request.app)
        except asyncio.CancelledError:
            hub.publish("patcher", did, f"[{patcher_id}] cancelled (shutting down)")
            raise
        except Exception as e:
            log.exception("Patcher %s %s failed for device %d", patcher_id, action, did)
            hub.publish("patcher", did, f"[{patcher_id}] FAILED: {e}")

    # Imported in the call rather than at module scope: `panel.py` owns the
    # background-task set this pins the run to, and it imports this module for
    # its route table — so the dependency only runs one way at import time.
    from petkit_local.web.panel import _spawn_background  # noqa: PLC0415

    _spawn_background(request.app, _run(), name=f"patcher-{patcher_id}-{action}-{did}")
    hub.publish("patcher", did, f"[{patcher_id}] {action} started")
    return web.json_response({"ok": True, "patcher": patcher_id, "action": action})


def _get_active_patchers(d: Device) -> set[str]:
    """Determine which patchers are currently active for a device, based on
    which patched files exist on /system (tracked in device config)."""
    return set(d.config.get("active_patchers", []))


def _save_active_patchers(d: Device, active: set[str], registry: DeviceRegistry) -> None:
    """Record the active patcher set on the device and persist it."""
    d.config["active_patchers"] = sorted(active)
    registry.save()


async def _patcher_apply(d: Device, patcher_id: str, device_ip: str, download_base: str,
                         hub: EventHub, app: web.Application) -> None:
    """Download the target file from the device, patch it, and put it back.

    Runs detached from the request (see `api_patcher_apply`) and narrates every
    step onto the hub, because from the UI's side this is a multi-minute silent
    operation otherwise. The `asyncio.sleep`s are the device's side of the
    handshake: each step waits out roughly one delivery-plus-execution interval
    before assuming the previous one ran. Over the heartbeat that includes the
    device's ~10s poll; over MQTT the command is pushed immediately and the wait
    is only execution time, so the sleeps are conservative rather than wrong.
    The binary is fetched over a temporary busybox httpd the device is told to
    start, and which is killed again even if patching raises.

    The final step rewrites /system/app_init.sh from the FULL active set and
    reboots, so patchers compose instead of overwriting each other.
    """
    did = d.petkit_id
    reg = app["registry"]
    # Which transport each run_cmd takes is decided per command by
    # `send_run_cmd`: a device that has joined MQTT stops polling the heartbeat
    # entirely, and the queue it used to be given would never be drained.
    bridge = app.get("bridge")
    cfg = app.get("cfg", {})
    data_dir = cfg.get("data_dir", "/data") if "data_dir" in cfg else "/data"
    P = f"[{patcher_id}]"
    storage_dir = await detect_patch_storage_dir(d, device_ip, bridge=bridge)
    reg.save()
    wrapper_path = app_init_wrapper_path(d)

    if patcher_id in ("mqtt", "cloud", "cacert"):
        hub.publish("patcher", did, f"{P} starting temp httpd on device...")
        await send_run_cmd(d, f"busybox httpd -p {DEVICE_HTTPD_PORT} -h /app/bin &", bridge)
        await asyncio.sleep(12)

        try:
            if patcher_id == "mqtt":
                hub.publish("patcher", did, f"{P} downloading ctrl from device...")
                binary = await download_from_device(device_ip, "ctrl")
                assert_download_plausible(binary, "ctrl")
                hub.publish("patcher", did, f"{P} downloaded ctrl ({len(binary)} bytes)")
                patched, offset = patch_ctrl(binary)
                hub.publish("patcher", did, f"{P} patched at offset 0x{offset:x} (md5={md5hex(patched)[:12]})")
                staged_name = "ctrl_patched"
                device_path = patched_file_path(staged_name, d)
            elif patcher_id == "cloud":
                hub.publish("patcher", did, f"{P} downloading cloud from device...")
                binary = await download_from_device(device_ip, "cloud")
                assert_download_plausible(binary, "cloud")
                hub.publish("patcher", did, f"{P} downloaded cloud ({len(binary)} bytes)")
                patched, applied = patch_cloud(binary)
                names = ", ".join(a["name"] for a in applied if a["status"] == "applied")
                hub.publish("patcher", did, f"{P} applied {len(applied)} patches: {names}")
                staged_name = "cloud_patched"
                device_path = patched_file_path(staged_name, d)
            else:
                hub.publish("patcher", did, f"{P} downloading ca.crt from device...")
                ca_data = await download_from_device(device_ip, "ca.crt")
                assert_download_plausible(ca_data, "ca.crt")
                hub.publish("patcher", did, f"{P} downloaded ca.crt ({len(ca_data)} bytes)")
                our_cert = load_our_cert(data_dir)
                patched = patch_ca_bundle(ca_data, our_cert)
                hub.publish("patcher", did, f"{P} appended our cert ({len(patched)} bytes)")
                staged_name = "ca_patched.crt"
                device_path = patched_file_path(staged_name, d)
        finally:
            # The space probe starts its OWN httpd on a different port, and
            # `killall httpd` cannot target one — so the download server has to
            # be gone before the probe runs, not merely before we finish.
            await send_run_cmd(d, "killall httpd 2>/dev/null", bridge)

        # Only now, with the patched bytes in hand, is the exact requirement
        # known. Checking before staging means a device that cannot fit the
        # write is left completely untouched.
        hub.publish("patcher", did, f"{P} checking free space on device...")
        hub.publish("patcher", did, f"{P} " + await ensure_space_for(
            d, device_ip, write_bytes=len(patched),
            targets=[device_path, wrapper_path], bridge=bridge,
            mount=storage_dir))

        stage_file(staged_name, patched, did)
        hub.publish("patcher", did, f"{P} uploading {staged_name} to device...")
        await asyncio.sleep(12)

        await send_run_cmd(d, f"wget -q -O {device_path} {download_base}/{staged_name} && chmod +x {device_path}", bridge)
        await asyncio.sleep(12)
        cleanup_staged(staged_name, did)
        hub.publish("patcher", did, f"{P} file uploaded to {device_path}")

    elif patcher_id == "ssh":
        pubkey = d.config.get("ssh_pubkey", "")
        if not pubkey:
            hub.publish("patcher", did, f"{P} FAILED: no public key configured")
            return

        hub.publish("patcher", did, f"{P} starting temp httpd on device...")
        await send_run_cmd(d, f"busybox httpd -p {DEVICE_HTTPD_PORT} -h /app/bin &", bridge)
        await asyncio.sleep(12)

        try:
            hub.publish("patcher", did, f"{P} downloading ctrl header to detect CPU...")
            ctrl_head = await download_from_device(device_ip, "ctrl")
            arch = elf_arch(ctrl_head)
            if not arch or arch not in SSH_ARCH_TO_BINARY:
                hub.publish("patcher", did,
                            f"{P} FAILED: ctrl is not a recognised architecture "
                            f"({arch or 'not an ELF'})")
                return
            bin_name = SSH_ARCH_TO_BINARY[arch]
            hub.publish("patcher", did, f"{P} device is {arch}, using {bin_name}")
        finally:
            await send_run_cmd(d, "killall httpd 2>/dev/null", bridge)
            await asyncio.sleep(2)

        bin_path = dropbear_path_for(arch)
        with open(bin_path, "rb") as f:
            dropbear = f.read()

        authkeys = (pubkey.strip() + "\n").encode()
        ssh_paths = patcher_device_files(SSH_PATCHER, d)
        hub.publish("patcher", did, f"{P} checking free space on device...")
        hub.publish("patcher", did, f"{P} " + await ensure_space_for(
            d, device_ip, write_bytes=len(dropbear) + len(authkeys) + DBKEY_RESERVE_BYTES,
            targets=[*ssh_paths, wrapper_path],
            bridge=bridge, mount=storage_dir))

        stage_file(bin_name, dropbear, did)
        stage_file(AUTHKEYS_STAGED_NAME, authkeys, did)
        hub.publish("patcher", did, f"{P} staged {bin_name} + authorized_keys for download")

        cmds = ssh_install_commands(download_base, bin_name, d)
        for i, cmd in enumerate(cmds, 1):
            hub.publish("patcher", did, f"{P} step {i}/{len(cmds)}: {cmd[:80]}...")
            await send_run_cmd(d, cmd, bridge)
            if not await wait_for_heartbeat(d, timeout=30):
                hub.publish("patcher", did, f"{P} step {i} timed out (device may not have polled)")
            await asyncio.sleep(12)

        cleanup_staged(bin_name, did)
        cleanup_staged(AUTHKEYS_STAGED_NAME, did)
        hub.publish("patcher", did, f"{P} dropbear installed, SSH should be reachable now")

    elif patcher_id == "talk":
        # Nothing to download or patch — the sink is a small stock-shell script
        # we ship. Install it ONCE into the patch store (persistent, cf. dropbear)
        # rather than regenerating it in /tmp at boot; the wrapper below only
        # starts the listener that runs it. The device fetches it from our staging
        # server, the same proven-unchanged path the wrapper itself takes.
        sink = TALK_SINK_SCRIPT.encode()
        sink_path = patched_file_path(TALK_SINK_NAME, d)
        hub.publish("patcher", did, f"{P} checking free space on device...")
        hub.publish("patcher", did, f"{P} " + await ensure_space_for(
            d, device_ip, write_bytes=len(sink),
            targets=[sink_path, wrapper_path], bridge=bridge, mount=storage_dir))

        stage_file(TALK_SINK_NAME, sink, did)
        hub.publish("patcher", did, f"{P} uploading sink script to {sink_path}...")
        await send_run_cmd(
            d,
            f"wget -q -O {sink_path} {download_base}/{TALK_SINK_NAME} && "
            f"chmod +x {sink_path}",
            bridge,
        )
        if not await wait_for_heartbeat(d, timeout=30):
            hub.publish("patcher", did, f"{P} sink upload timed out - staged file kept for retry")
        else:
            await asyncio.sleep(15)
            cleanup_staged(TALK_SINK_NAME, did)

    else:
        # camera writes no file of its own — but the wrapper below is still a
        # write to persistent storage, and a full filesystem is exactly why
        # that would fail silently, leaving the patch marked active but inert.
        hub.publish("patcher", did, f"{P} checking free space on device...")
        hub.publish("patcher", did, f"{P} " + await ensure_space_for(
            d, device_ip, write_bytes=0, targets=[wrapper_path], bridge=bridge,
            mount=storage_dir))

    active = _get_active_patchers(d)
    active.add(patcher_id)
    _save_active_patchers(d, active, reg)

    hub.publish("patcher", did, f"{P} uploading {wrapper_path} wrapper...")
    wrapper_content = generate_app_init_wrapper(active, d)
    stage_file("app_init.sh", wrapper_content.encode(), did)
    await send_run_cmd(
        d,
        f"wget -q -O {wrapper_path} {download_base}/app_init.sh && "
        f"chmod +x {wrapper_path}",
        bridge,
    )
    # Wait for the device to actually fetch the file BEFORE cleaning it up.
    # The old code cleaned after a fixed 15 s, which raced with the heartbeat:
    # the device polls every ~10 s, the command reaches it on the next poll, and
    # the wget runs after that — easily 20+ s total. A 404 meant the wrapper
    # was never delivered, and the device rebooted into a stale one.
    if not await wait_for_heartbeat(d, timeout=30):
        hub.publish("patcher", did, f"{P} wrapper upload timed out - staged file kept for retry")
    else:
        await asyncio.sleep(15)
        cleanup_staged("app_init.sh", did)

    hub.publish("patcher", did, f"{P} rebooting device...")
    await send_run_cmd(d, "reboot", bridge)
    hub.publish("patcher", did, f"{P} done - device will reboot, patch active on next boot")


async def _patcher_remove(d: Device, patcher_id: str, device_ip: str, download_base: str,
                          hub: EventHub, app: web.Application) -> None:
    """Drop one patcher from the active set and reboot the device into it.

    Nothing is downloaded or patched here — removal is just deleting the
    patched files and rewriting the app_init.sh wrapper from the REMAINING set,
    so removing one patcher leaves the others working. With none left the
    wrapper itself is removed and the device boots stock again.
    """
    did = d.petkit_id
    reg = app["registry"]
    bridge = app.get("bridge")  # see _patcher_apply
    P = f"[{patcher_id}]"
    await detect_patch_storage_dir(d, device_ip, bridge=bridge)
    reg.save()
    wrapper_path = app_init_wrapper_path(d)

    active = _get_active_patchers(d)
    active.discard(patcher_id)
    _save_active_patchers(d, active, reg)

    pinfo = ALL_PATCHERS[patcher_id]
    # A pure bind-mount patcher (camera) puts no file on the device, and
    # busybox `rm -f` with no operands prints its usage and exits non-zero —
    # which would be a confusing failure for a step that has nothing to do.
    file_paths = patcher_device_files(pinfo, d)
    files = " ".join(file_paths)
    cleanup_cmds = f"rm -f {files}" if files else "true"

    if active:
        hub.publish("patcher", did, f"{P} uploading updated wrapper...")
        wrapper_content = generate_app_init_wrapper(active, d)
        stage_file("app_init.sh", wrapper_content.encode(), did)
        # Delete the patched files and download the new wrapper in one command,
        # so the device never boots with a wrapper that references files that
        # no longer exist.
        await send_run_cmd(
            d,
            f"{cleanup_cmds}; "
            f"wget -q -O {wrapper_path} {download_base}/app_init.sh && "
            f"chmod +x {wrapper_path}",
            bridge,
        )
        # Same wait-then-cleanup as apply: the staged file must survive until
        # the device actually fetches it.
        if not await wait_for_heartbeat(d, timeout=30):
            hub.publish("patcher", did, f"{P} wrapper upload timed out - staged file kept for retry")
        else:
            await asyncio.sleep(15)
            cleanup_staged("app_init.sh", did)
        hub.publish("patcher", did, f"{P} rebooting device...")
        await send_run_cmd(d, "reboot", bridge)
    else:
        await send_run_cmd(
            d, f"{cleanup_cmds}; {build_wrapper_remove_cmd(d)} && reboot",
            bridge)

    hub.publish("patcher", did, f"{P} removal queued - device will reboot")
