# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Telling Luna the Deck is a supported OS, and keeping it told.

Luna has two gates reading different things: the browser gate on userAgent (the
--user-agent flag) and the OS gate on navigator.platform / uaData. Only
Emulation.setUserAgentOverride moves all of them together. The override lives
with the CDP session that set it, so hold_platform returns the live socket open
and main() keeps it until the browser exits.
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


def test_the_override_alone_enables_no_domain(fake):
    """Nothing reads this socket, so a domain enabled needlessly is an event
    stream into a buffer no one drains."""
    held = cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    cdp.reload_page(held)
    assert "Page.enable" not in [m["method"] for m in held.sent]


def test_a_startup_script_must_enable_the_page_domain(fake):
    """Without Page.enable first the script never runs, yet every signal still
    says sucess. So the enable must come before the script."""
    held = cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    cdp.add_startup_script(held, "window.x = 1;")
    methods = [m["method"] for m in held.sent]
    assert methods.index("Page.enable") < \
           methods.index("Page.addScriptToEvaluateOnNewDocument")


def test_the_drain_survives_the_socket_closing(fake):
    """It ends when the session does; it must not raise on the way out."""
    held = cdp.hold_platform("ws://127.0.0.1:9223/x", UA, "Windows")
    thread = cdp.drain_in_background(held)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert thread.daemon


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
    # scripts must be registered before the reload, or Luna's document never
    # gets them. count of scripts can vary (handoff shim, gamepad fix).
    assert methods[0] == "Emulation.setUserAgentOverride"
    assert "Page.enable" in methods
    assert "Page.addScriptToEvaluateOnNewDocument" in methods
    assert methods[-1] == "Page.reload"
    reload_idx = methods.index("Page.reload")
    for i, m in enumerate(methods):
        if m == "Page.addScriptToEvaluateOnNewDocument":
            assert i < reload_idx, "startup script registered after reload"


def test_a_missing_page_is_survivable(monkeypatch):
    """Best effort, like autoplay: the user presses Play themselves and gets
    the behaviour they would have had anyway. Never fatal."""
    monkeypatch.setattr(cdp, "wait_for_page", lambda *a, **k: None)
    assert launcher.hold_platform_override(
        "https://luna.amazon.com/game/x", "amazon_luna") is None


# ---------------------------------------------- it must outlive the setup

def test_main_holds_the_override_until_the_browser_exits():
    """`held` looks unused but it IS the override. Dropping it, or closing it
    before browser.wait(), reverts the platform mid-session."""
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


# ------------------------------------- the luna:// app handoff / xdg-open

def test_the_handoff_stub_supplies_the_id_luna_looks_for():
    """Luna does document.querySelector("#hiddenIframe") and only creates its
    own when that returns null, so the id is the entire hook."""
    js = launcher.NEUTRALISE_APP_HANDOFF_JS
    assert "hiddenIframe" in js
    assert "contentWindow" in js


def test_the_stub_swallows_the_href_assignment():
    """openUriWithHiddenFrame ends in `frame.contentWindow.location.href = uri`.
    A setter that does nothing is what turns that in silence."""
    js = launcher.NEUTRALISE_APP_HANDOFF_JS
    assert "set href" in js


def test_the_stub_waits_for_a_dom_to_attach_to():
    """A start script runs before the page's own, when documentElement can be
    null. Luna only looks for #hiddenIframe on Play, so DOMContentLoaded is
    early enough."""
    js = launcher.NEUTRALISE_APP_HANDOFF_JS
    assert "DOMContentLoaded" in js
    assert "document.body || document.documentElement" in js


def test_the_stub_never_breaks_the_page():
    assert "try {" in launcher.NEUTRALISE_APP_HANDOFF_JS
    assert "catch" in launcher.NEUTRALISE_APP_HANDOFF_JS


def test_only_luna_gets_the_stub():
    """Nothing else pokes a custom scheme, and injecting into a provider that
    does not need it is a way to break one that works."""
    assert launcher.provider_profile("amazon_luna").get("startup_js")
    for key in ("boosteroid", "geforce_now", "xbox_cloud"):
        assert not launcher.provider_profile(key).get("startup_js")


def test_the_stub_runs_in_a_real_engine(tmp_path):
    """Prove the shim really swallows the assignment. Mirrors what
    openUriWithHiddenFrame does."""
    import json as _json, shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = """
const els = {};
const make = () => ({ id:"", style:{}, _props:{},
  appendChild(){}, });
global.document = {
  createElement: () => { const e = make();
     e.__defineSetterSafe = true; return e; },
  getElementById: id => els[id] || null,
  querySelector: sel => els[sel.replace('#','')] || null,
  documentElement: { appendChild(el){ els[el.id] = el; } },
};
global.Object = Object;
%s
// now do exactly what Luna does
const frame = document.querySelector("#hiddenIframe");
let threw = false, landed = null;
try { frame.contentWindow.location.href = "luna://play.amazon.com/x"; }
catch (e) { threw = true; }
try { landed = frame.contentWindow.location.href; } catch (e) {}
console.log(JSON.stringify({ found: !!frame, threw, landed }));
""" % launcher.NEUTRALISE_APP_HANDOFF_JS
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    result = _json.loads(out.stdout.strip())
    assert result["found"] is True, "Luna would have made its own iframe"
    assert result["threw"] is False, "an exception is not the mechanism"
    assert result["landed"] == "", "the scheme must go nowhere"


# ------------------------------------------------ gamepad diagnostics

def test_pad_names_are_kept_not_just_paths(monkeypatch, tmp_path):
    """Steam renumbers its virtual pads every session, so an event path
    identifies nothing. "pad 0"/"pad 1" is Steam's slot order, and slot order
    decides player one."""
    devices = tmp_path / "devices"
    devices.write_text(
        'I: Bus=0003\nN: Name="Microsoft X-Box 360 pad 0"\nH: Handlers=event18 js0\n'
        '\n'
        'I: Bus=0003\nN: Name="Microsoft X-Box 360 pad 1"\nH: Handlers=event19 js1\n'
        '\n'
        'I: Bus=0005\nN: Name="Steam Ctrl (BT) FXA99547006E5"\nH: Handlers=event24\n')
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/proc/bus/input/devices":
            return real_open(devices, *a, **k)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    pads = launcher.active_gamepads()
    assert any("pad 0" in p for p in pads)
    assert any("pad 1" in p for p in pads)
    # the raw Bluetooth device is not a Steam Input virtual pad
    assert not any("Steam Ctrl (BT)" in p for p in pads)


def test_the_diagnostic_never_breaks_a_launch(monkeypatch):
    """A diagnostic that can abort a game is worse than no diagnostic"""
    monkeypatch.setattr(cdp, "wait_for_page", lambda *a, **k: None)
    launcher.log_browser_gamepads("https://luna.amazon.com/game/x", "amazon_luna")


def test_a_cdp_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cdp, "wait_for_page",
                        lambda *a, **k: {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/x"})
    def boom(*a, **k):
        raise cdp.CDPError("page went away")
    monkeypatch.setattr(cdp, "evaluate", boom)
    launcher.log_browser_gamepads("https://luna.amazon.com/game/x", "amazon_luna")


# ------------------------------------------- waiting for Steam Input's pads

class FakeTime:
    """A clock that only moves when something sleeps."""
    def __init__(self):
        self.now = 0.0
    def monotonic(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def _with_pads(monkeypatch, sequence):
    """active_gamepads() answers the next entry on each call, then repeats."""
    calls = {"i": 0}
    def fake():
        i = min(calls["i"], len(sequence) - 1)
        calls["i"] += 1
        return list(sequence[i])
    monkeypatch.setattr(launcher, "active_gamepads", fake)
    clock = FakeTime()
    monkeypatch.setattr(launcher, "time", clock)
    return clock


def test_it_waits_for_the_SECOND_pad_not_just_the_first(monkeypatch):
    """Returning on pad 0 could launch before pad 1 existed, leaving the
    external controller invisible for the whole session."""
    _with_pads(monkeypatch, [[], ["pad 0"], ["pad 0", "pad 1"]])
    assert launcher.wait_for_gamepad() == ["pad 0", "pad 1"]


def test_a_single_controller_still_returns(monkeypatch):
    _with_pads(monkeypatch, [["pad 0"]])
    assert launcher.wait_for_gamepad() == ["pad 0"]


def test_no_controller_at_all_is_not_an_error(monkeypatch):
    """Returns [] rather than hanging or raising; the caller only logs it."""
    _with_pads(monkeypatch, [[]])
    assert launcher.wait_for_gamepad(timeout=2.0) == []


def test_it_cannot_wait_forever(monkeypatch):
    """Pads that keep churning must not hold a launch open indefinetly."""
    churn = [["a"], ["a", "b"], ["a"], ["a", "b"], ["a"]] * 20
    clock = _with_pads(monkeypatch, churn)
    launcher.wait_for_gamepad(timeout=3.0)
    assert clock.now <= 4.0, "must give up at the timeout"


# ------------------------------------- gamepad slot 0 is empty, so fill it

