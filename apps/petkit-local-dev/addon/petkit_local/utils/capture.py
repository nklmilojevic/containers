"""Raw payload capture for reverse-engineering / parser tuning.

When capture mode is on, raw HTTP state reports and MQTT messages are appended
to JSONL files under the capture directory. Each line is one timestamped record.
This is the fastest way to turn a first real-device session into concrete
`state_parsers` / bridge tuning instead of guessing.

**A capture is SENSITIVE and is never redacted.** Redaction (`http/redact/`)
sanitises what is sent *to a device*; it does not touch what is written here,
because the whole value of a capture is that it is verbatim. What each stream
can contain:

    requests            every device request/response — the X-Device signature
    state_report_raw    the unparsed report: Wi-Fi SSID, the device's LAN IP, sn
    state_report        the same, normalised
    mqtt                every device MQTT frame
    proxy_http          FULL request AND response bodies to/from PetKit,
                        including the account credentials the real cloud issues
    proxy_mqtt          the same for the upstream MQTT session
    proxy_redactions    what was stripped on the way to the device — i.e. a
                        record of precisely the hostile/secret values

`proxy_http` and `proxy_mqtt` are the dangerous ones: a capture taken in proxy
mode is enough for someone else to talk to PetKit's cloud as you.

Anything that offers a capture to a user — the panel's Capture tab, the README's
contributing section — has to say so, because "attach your capture to the issue"
is otherwise an instruction to publish all of the above.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def capture_record(capture_dir: str, name: str, record: dict[str, Any]) -> None:
    """Append one record to `{capture_dir}/{name}.jsonl`, as a single JSON line.

    A `ts` (unix seconds) key is added; a key of that name in `record` wins.
    Values that JSON cannot represent are stringified rather than refused.

    Never raises: capture is a debugging aid that runs on the device-facing
    path, so a full disk or an unserialisable payload must cost a log line, not
    the request that was being captured.
    """
    try:
        d = Path(capture_dir)
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.time(), **record}, default=str)
        with (d / f"{name}.jsonl").open("a") as f:
            f.write(line + "\n")
    except Exception as e:  # pragma: no cover - best effort, must not break flow
        log.debug("capture failed (%s): %s", name, e)
