# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Telling Luna the Deck is a supported OS, and keeping it told.

Luna has TWO gates that read DIFFERENT things, which is why fixing the first
one simply revealed the second:

    "Unsupported Browser"  navigator.userAgent          --user-agent flag
    "Unsupported OS"       navigator.platform / uaData  this override

Measured on the device with the --user-agent flag already on:

    ua         Windows NT 10.0; Win64; x64      <- flag worked
    platform   Linux x86_64                     <- flag does not reach this
    uaData     Linux                            <- nor this

and --disable-features=UserAgentClientHint changed neither, so there is no
command-line route. Emulation.setUserAgentOverride is the only thing that moves
all of them together.

The override belongs to the CDP SESSION that set it. Measured, same page:

    held socket open, read from a SECOND socket   platform Windows
    held socket closed                            platform Linux x86_64

which is why hold_platform returns the live socket instead of closing it, and
why main() keeps the reference until the browser exits.

Run: python3 -m pytest tests/unit/test_platform_override.py
"""

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, ROOT)

import cdp  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "launcher", os.path.join(ROOT, "bin", "shelfcloud-launch.py"))
launcher = importlib.util.module_from_spec(_spec)
sys.argv = ["x"]
_spec.loader.exec_module(launcher)


class FakeSocket:
    """Stands in for a CDP peer: records what was sent, replies to each id."""

    def __init__(self, url="ws://127.0.0.1:9223/x", timeout=15.0):
        self.sent = []
        self.closed = False
        self._replies = []

    def send_text(self, payload):
        message = json.loads(payload)
        self.sent.append(message)
        self._replies.append(json.dumps({"id": message["id"], "result": {}}))

    def recv_text(self):
        if not self._replies:
            raise cdp.CDPError("nothing to read")
        return self._replies.pop(0)

    def close(self):
        self.closed = True


@pytest.fixture
def fake(monkeypatch):
    made = []

    def factory(url, timeout=15.0):
        sock = FakeSocket(url, timeout)
        made.append(sock)
        return sock

    monkeypatch.setattr(cdp, "_WebSocket", factory)
    return made


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Edg/153.0.0.0"


# ------------------------------------------------- what goes over the wire

def test_the_override_sets_every_os_signal_at_once(fake):
    cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    params = fake[0].sent[0]["params"]

    assert fake[0].sent[0]["method"] == "Emulation.setUserAgentOverride"
    assert params["userAgent"] == UA                      # navigator.userAgent
    assert params["platform"] == "Windows"                # navigator.platform
    assert params["userAgentMetadata"]["platform"] == "Windows"   # uaData
    assert params["userAgentMetadata"]["mobile"] is False


def test_the_socket_is_returned_open(fake):
    """The override dies with the session, so closing it here would undo the
    whole fix. Measured: platform reverts to "Linux x86_64" on close."""
    held = cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    assert held is fake[0]
    assert fake[0].closed is False


def test_a_refused_override_does_not_leak_the_socket(fake, monkeypatch):
    def bad_recv(self=None):
        return json.dumps({"id": 1, "error": {"message": "nope"}})
    monkeypatch.setattr(FakeSocket, "recv_text", bad_recv)

    with pytest.raises(cdp.CDPError):
        cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    assert fake[0].closed is True


def test_reload_does_not_enable_the_page_domain(fake):
    """Nothing reads this socket after setup. Enabling a domain would start an
    event stream into a buffer no one drains, and a peer that cannot write
    eventually blocks."""
    held = cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    cdp.reload_page(held)
    methods = [m["method"] for m in held.sent]
    assert "Page.reload" in methods
    assert "Page.enable" not in methods


# ------------------------------------------------- who gets the override

def test_only_luna_asks_for_one():
    assert launcher.provider_profile("amazon_luna").get("user_agent")
    for key in ("boosteroid", "geforce_now", "xbox_cloud"):
        assert not launcher.provider_profile(key).get("user_agent")


def test_a_provider_without_a_user_agent_is_left_alone(monkeypatch):
    """No socket, no reload, no wasted PAGE_TIMEOUT waiting for a page."""
    monkeypatch.setattr(cdp, "wait_for_page",
                        lambda *a, **k: pytest.fail("should not look for a page"))
    assert launcher.hold_platform_override("https://www.xbox.com/play",
                                           "xbox_cloud") is None


def test_luna_gets_one_and_the_page_is_reloaded(fake, monkeypatch):
    monkeypatch.setattr(cdp, "wait_for_page",
                        lambda *a, **k: {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/x"})
    held = launcher.hold_platform_override(
        "https://luna.amazon.com/game/fortnite", "amazon_luna")

    assert held is not None and held.closed is False
    methods = [m["method"] for m in held.sent]
    assert methods == ["Emulation.setUserAgentOverride", "Page.reload"]


def test_a_missing_page_is_survivable(monkeypatch):
    """Best effort, like autoplay: the user presses Play themselves and gets
    the behaviour they would have had anyway. Never fatal."""
    monkeypatch.setattr(cdp, "wait_for_page", lambda *a, **k: None)
    assert launcher.hold_platform_override(
        "https://luna.amazon.com/game/x", "amazon_luna") is None


# ---------------------------------------------- it must outlive the setup

def test_main_holds_the_override_until_the_browser_exits():
    """`held` looks like an unused variable and is not: it IS the override.
    Dropping it, or closing it before browser.wait(), reverts the platform
    mid-session — measured, not assumed."""
    import inspect
    source = inspect.getsource(launcher.main)
    hold = source.index("hold_platform_override")
    wait = source.index("browser.wait()")
    close = source.index("held.close()")
    assert hold < wait < close, "the override must span the whole session"


def test_the_override_is_applied_even_without_autoplay():
    """The OS gate blocks a Play the user presses by hand just as surely as one
    the launcher presses, so this cannot sit inside `if autoplay`."""
    import inspect, re
    source = inspect.getsource(launcher.main)
    hold_line = source.index("held = hold_platform_override")
    autoplay_guard = source.index("if autoplay:")
    assert hold_line < autoplay_guard
