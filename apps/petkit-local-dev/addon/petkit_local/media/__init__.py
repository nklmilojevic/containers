"""The media pipeline: raw encrypted uploads to a playable, browsable tree.

What the device PUTs into the local bucket is not usable as-is - it is
AES-128-CBC ciphertext, and the video arrives as a stream of ~4 second chunks.
This package is the chain that fixes that: `crypto.py` decrypts, `transcode.py`
remuxes, `layout.py` decides the friendly path, `pipeline.py` runs those in
order and records the result, `stitch.py` later joins an episode's chunks into
one clip, and `retention.py` enforces the per-capability disk caps.

Two rules here are load-bearing and easy to get wrong: a media ROLE (derived
from `moduleType`) is not the same thing as an STS CAPABILITY, and two video
roles covering the same timespan (the main stream and its time-lapse
substream) must never be joined together.
"""
