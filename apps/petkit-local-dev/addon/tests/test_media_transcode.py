import asyncio

from petkit_local.media import transcode


def test_have_ffmpeg_returns_bool_and_caches():
    transcode._have_ffmpeg_cache = None
    result = transcode.have_ffmpeg()
    assert isinstance(result, bool)
    assert transcode.have_ffmpeg() == result


def test_remux_returns_false_without_ffmpeg():
    orig = transcode.have_ffmpeg
    transcode.have_ffmpeg = lambda: False
    try:
        ok = asyncio.run(transcode.remux_ts_to_mp4("/nonexistent/src.ts", "/nonexistent/dst.mp4"))
        assert ok is False
    finally:
        transcode.have_ffmpeg = orig


def test_probe_returns_dict_for_missing_file():
    result = asyncio.run(transcode.probe("/nonexistent/path/does-not-exist.ts"))
    assert result == {}
