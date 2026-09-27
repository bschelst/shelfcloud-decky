#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Every plugin call against a gateway that answers badly.

`_request` just json.loads whatever comes back, so a proxy error page, a stale
shape or a dropped connection must not raise -- Decky turns an exception into a
dead panel. This is a sweep: every public method against every bad answer, and
the only check is that it returns rather than raises.
"""

import asyncio
import inspect
import os
import sys
import types
from unittest import mock

import pytest

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = "/plugins/Shelfcloud"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/settings/Shelfcloud"
decky.logger = mock.MagicMock()
decky.emit = mock.AsyncMock()
decky.DECKY_PLUGIN_VERSION = "0.0.0-test"
sys.modules.setdefault("decky", decky)

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402


# Everything a gateway has actually been seen to answer, or could.
HOSTILE = {
    "none": lambda *a, **k: None,
    "empty string": lambda *a, **k: "",
    "a list": lambda *a, **k: [],
    "a string": lambda *a, **k: "not json at all",
    "a number": lambda *a, **k: 0,
    "true": lambda *a, **k: True,
    "an empty dict": lambda *a, **k: {},
    "a dict of nothing useful": lambda *a, **k: {"unexpected": "shape"},
    "nulls in every field": lambda *a, **k: {
        "games": None, "tracked": None, "added": None, "removed": None,
        "shelves": None, "ok": None, "status": None,
    },
    "games not a list": lambda *a, **k: {"games": {"nope": 1}},
    "games full of rubbish": lambda *a, **k: {
        "games": [None, 1, "x", [], {"no": "id"}, {"game_id": "abc"}],
    },
    "deeply wrong game rows": lambda *a, **k: {
        "games": [{"game_id": 1, "providers": "not a list", "name": None}],
    },
}


def _raiser(exc):
    def boom(*a, **k):
        raise exc
    return boom


FAILURES = {
    "connection refused": _raiser(OSError("connection refused")),
    "timeout": _raiser(TimeoutError("timed out")),
    "bad json (an html error page)": _raiser(
        __import__("json").JSONDecodeError("Expecting value", "<html>", 0)),
    "signed out": _raiser(PermissionError("signed out")),
    "something unforeseen": _raiser(RuntimeError("boom")),
}


@pytest.fixture(autouse=True)
def _off_the_real_machine(tmp_path, monkeypatch):
    """Keep the sweep off real dirs. Some calls write files, and a
    PermissionError on the real machine would look like the crash we hunt."""
    monkeypatch.setattr(main.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(main.shortcuts, "shortcuts_files", lambda *a, **k: [])


def a_plugin():
    instance = main.Plugin()
    instance._settings = {"token": "t", "gateway_url": "https://gw.test"}
    instance._synced = {}
    instance._save_settings = mock.AsyncMock()
    instance._save_state = mock.AsyncMock()
    return instance


# Every method taking only self, collected from the class so a new one is
# coverd without editing this list.
def no_argument_calls():
    for name, member in vars(main.Plugin).items():
        if name.startswith("_") or not inspect.iscoroutinefunction(member):
            continue
        signature = inspect.signature(member)
        if len(signature.parameters) == 1:            # just self
            yield name


@pytest.mark.parametrize("answer", list(HOSTILE), ids=list(HOSTILE))
@pytest.mark.parametrize("name", list(no_argument_calls()))
def test_a_bad_answer_never_raises(name, answer):
    plugin = a_plugin()
    plugin._request = HOSTILE[answer]
    result = asyncio.run(getattr(plugin, name)())
    assert isinstance(result, dict), (
        f"{name} answered {type(result).__name__} for a {answer} response; "
        f"the frontend reads .ok off it")


@pytest.mark.parametrize("failure", list(FAILURES), ids=list(FAILURES))
@pytest.mark.parametrize("name", list(no_argument_calls()))
def test_a_failing_gateway_never_raises(name, failure):
    plugin = a_plugin()
    plugin._request = FAILURES[failure]
    result = asyncio.run(getattr(plugin, name)())
    assert isinstance(result, dict), f"{name} did not answer a dict on {failure}"


# ------------------------------------------------- the calls that take input

@pytest.mark.parametrize("answer", list(HOSTILE), ids=list(HOSTILE))
def test_drop_from_library_survives_a_bad_answer(answer):
    plugin = a_plugin()
    plugin._request = HOSTILE[answer]
    assert isinstance(asyncio.run(plugin.drop_from_library([1, 2, 3])), dict)


@pytest.mark.parametrize("rubbish", [
    None, "", [], {}, 0, "abc", [None], [{}], [[1]], [float("nan")],
    [10**30], ["-1"], [True],
])
def test_drop_from_library_survives_rubbish_input(rubbish):
    """The frontend builds this list from a gateway response, so is untrusted."""
    sent = []
    plugin = a_plugin()
    plugin._request = lambda *a, **k: sent.append(k or a) or {"removed": 0}
    assert isinstance(asyncio.run(plugin.drop_from_library(rubbish)), dict)


@pytest.mark.parametrize("rubbish", [None, "", [], {}, 0, "abc", -1, 10**30])
def test_forget_shortcut_survives_rubbish_input(rubbish):
    plugin = a_plugin()
    assert isinstance(asyncio.run(plugin.forget_shortcut(rubbish)), dict)


@pytest.mark.parametrize("rubbish", [None, "", [], {}, 0, "not base64", "=="])
def test_save_icon_survives_rubbish_input(rubbish):
    plugin = a_plugin()
    result = asyncio.run(plugin.save_icon(1, rubbish, "png"))
    assert isinstance(result, dict) and result.get("ok") is False


@pytest.mark.parametrize("url", [
    None, "", [], {}, 0, "not a url", "ftp://x/y", "http://insecure.test/a",
    "javascript:alert(1)",
])
def test_fetch_artwork_refuses_anything_but_https(url):
    plugin = a_plugin()
    result = asyncio.run(plugin.fetch_artwork(url))
    assert isinstance(result, dict) and result.get("ok") is False


# ------------------------------------------------------- the response itself

def test_a_reply_is_read_with_a_ceiling():
    """`_request` caps the read: a portal answering an endless stream should
    fail, not fill the Deck's memory."""
    class Endless:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        @staticmethod
        def read(size=-1):
            # honour the size arg, else an unbounded read hangs the test
            return b"x" * (size if size and size > 0 else 1024)

        headers = {"Content-Type": "application/json"}

    plugin = a_plugin()
    # patched at _open, the shared pool every request goes through
    with mock.patch.object(main, "_open", lambda *a, **k: Endless()):
        with pytest.raises(ValueError, match="more than"):
            plugin._request("/health", authenticated=False)
