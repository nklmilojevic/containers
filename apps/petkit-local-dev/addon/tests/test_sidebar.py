"""Putting the panel in Home Assistant's sidebar, once.

The panel IS this add-on's interface, and a fresh install hides it: "Show in
sidebar" is per-install state the Supervisor keeps, defaulting to off, with no
`config.yaml` key to change it. So it is set through the Supervisor API — but
exactly once, because an add-on that reinstates itself in the sidebar every time
someone removes it is worse than the problem it solves.
"""
import json

from petkit_local.config import SIDEBAR_FLAG_FILENAME, show_in_sidebar_once


class _Recorder:
    """Stands in for urlopen, capturing what would have been sent."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if self.fail:
            raise OSError("supervisor unreachable")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, tmp_path, *, token="tok", fail=False):
    import petkit_local.config as cfg

    rec = _Recorder(fail=fail)
    monkeypatch.setattr(cfg.urllib.request, "urlopen", rec)
    if token is None:
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SUPERVISOR_TOKEN", token)
    return rec


def test_it_asks_the_supervisor_to_show_the_panel(monkeypatch, tmp_path):
    rec = _patch(monkeypatch, tmp_path)
    show_in_sidebar_once(str(tmp_path))

    assert len(rec.calls) == 1
    req = rec.calls[0]
    assert req.full_url == "http://supervisor/addons/self/options"
    assert req.get_method() == "POST"
    assert json.loads(req.data) == {"ingress_panel": True}


def test_it_never_asks_twice(monkeypatch, tmp_path):
    """Otherwise it would put itself back in the sidebar on every restart,
    overriding the operator every time they took it out."""
    rec = _patch(monkeypatch, tmp_path)
    show_in_sidebar_once(str(tmp_path))
    show_in_sidebar_once(str(tmp_path))
    show_in_sidebar_once(str(tmp_path))
    assert len(rec.calls) == 1
    assert (tmp_path / SIDEBAR_FLAG_FILENAME).exists()


def test_a_failed_call_is_retried_next_start(monkeypatch, tmp_path):
    """The flag records that it WORKED, not that it was attempted — a Supervisor
    that was briefly unreachable must not cost the panel its sidebar entry
    forever."""
    rec = _patch(monkeypatch, tmp_path, fail=True)
    show_in_sidebar_once(str(tmp_path))
    assert not (tmp_path / SIDEBAR_FLAG_FILENAME).exists()

    rec.fail = False
    show_in_sidebar_once(str(tmp_path))
    assert (tmp_path / SIDEBAR_FLAG_FILENAME).exists()
    assert len(rec.calls) == 2


def test_it_does_nothing_without_a_supervisor(monkeypatch, tmp_path):
    """Running as a plain container, there is no sidebar and no API to call."""
    rec = _patch(monkeypatch, tmp_path, token=None)
    show_in_sidebar_once(str(tmp_path))
    assert rec.calls == []
    assert not (tmp_path / SIDEBAR_FLAG_FILENAME).exists()


def test_a_supervisor_error_never_raises(monkeypatch, tmp_path):
    """Failing to tidy the sidebar must not stop the add-on starting."""
    _patch(monkeypatch, tmp_path, fail=True)
    show_in_sidebar_once(str(tmp_path))  # must not raise


def test_only_the_addon_path_calls_it():
    """A standalone run has no Supervisor, so the call is gated on --ha-addon
    rather than left to fail its own way in."""
    import inspect

    from petkit_local import main as main_mod

    src = inspect.getsource(main_mod.main)
    assert "show_in_sidebar_once" in src
    idx = src.index("show_in_sidebar_once")
    assert "args.ha_addon" in src[max(0, idx - 400):idx]
