#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Every call the plugin makes, against a gateway that answers badly.

The plugin talks to one HTTP service and then hands what comes back to Steam.
Between those two points there is no schema — `_request` does `json.loads` and
returns whatever that produced — so "the response is a dict with the fields I
expect" is an assumption, not a fact, and the ways it breaks are ordinary:

  * the proxy in front of the gateway replaces an origin 404 with its own
    branded page and serves it as **HTTP 200, text/html**, so a missing route
    arrives as a JSON parse error rather than a 404
  * a captive portal or a Cloudflare error page does the same
  * a half-deployed gateway answers 200 with an older shape
  * a connection drops mid-read

None of those may raise out of a plugin method. Decky surfaces an exception
from a callable as a dead panel, and the user has no way to see why.

So this is a sweep rather than a list of cases: every public method is called
against every hostile answer, and the only assertion is that it returns rather
than raises. A method added later is covered the day it is written.

Run: python3 -m pytest tests/unit
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
    """Keep the sweep off real directories.

    Some of these calls write files: an icon, the settings, the tracked state.
    Left alone they reach DECKY_PLUGIN_SETTINGS_DIR and Steam's own userdata on
    whatever machine runs the tests, and a PermissionError there looks exactly
    like the crash this file is hunting for.
    """
    monkeypatch.setattr(main.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(tmp_path))
    monkeypatch.setattr(main.shortcuts, "shortcuts_files", lambda *a, **k: [])


def a_plugin():
    instance = main.Plugin()
    instance._settings = {"token": "t", "gateway_url": "https://gw.test"}
    instance._synced = {}
    instance._save_settings = mock.AsyncMock()
    instance._save_state = mock.AsyncMock()
    return instance


# The methods that take no argument but a self, which is every call the
# frontend makes with no input. Collected from the class so a new one is
# covered without editing this list.
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
    """The frontend builds this list from a gateway response, so it is only as
    trustworthy as that response was."""
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
    """`_request` used to read the whole body however long it was, trusting
    whatever answered to be reasonable about size. The biggest thing it asks
    for is a catalogue of a few megabytes; a captive portal answering a stream
    should fail rather than fill the Deck's memory."""
    class Endless:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        @staticmethod
        def read(size=-1):
            # Honours the argument, which is the point: an unbounded read here
            # would hang this test rather than fail it.
            return b"x" * (size if size and size > 0 else 1024)

        headers = {"Content-Type": "application/json"}

    plugin = a_plugin()
    with mock.patch.object(main.urllib.request, "urlopen",
                           lambda *a, **k: Endless()):
        with pytest.raises(ValueError, match="more than"):
            plugin._request("/health", authenticated=False)
