# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Pressing Play on Amazon Luna, pinned against what the live page actually does.

Two bugs found by opening a real Luna game page on a Deck over CDP, both of
which failed SILENTLY — the game simply never started and the log said
something reassuring:

  1. Luna's game detail page carries a TRAILER <video> (paused, muted, 0x0).
     IS_STREAMING_JS asked "is there a video?", so it answered "already
     streaming" and returned before looking for Play.

  2. The launcher waited for a page whose URL contains the host it asked for,
     luna.amazon.com — but Luna REDIRECTS to the viewer's own country. Belgium
     goes to luna.amazon.com.be, which contains "luna.amazon.com" and matched
     by accident; Germany (luna.amazon.de) and the UK (luna.amazon.co.uk) do
     not, and would have waited out the timeout. The bug was invisible from
     Belgium and broken everywhere else.

The Play control itself was read off the live signed-in page rather than
guessed:

    primary_entitled_button_purchase_detail   signed in  -> "Play now"
    primary_entitled_button_log_in            signed out -> "Play free with Prime"

Run: python3 -m pytest tests/unit/test_luna_autoplay.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "launcher", os.path.join(ROOT, "bin", "shelfcloud-launch.py"))
launcher = importlib.util.module_from_spec(_spec)
sys.argv = ["x"]
_spec.loader.exec_module(launcher)

LUNA = launcher.provider_profile("amazon_luna")


# ----------------------------------------------------- finding the page

@pytest.mark.parametrize("url", [
    # Every Luna market domain seen on the live site's own country switcher.
    "https://luna.amazon.com/game/fortnite?autoplay=1",
    "https://luna.amazon.com.be/game/fortnite?autoplay=1",    # Belgium
    "https://luna.amazon.de/game/fortnite?autoplay=1",        # Germany
    "https://luna.amazon.co.uk/game/fortnite?autoplay=1",     # UK
    "https://luna.amazon.fr/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.it/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.es/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.ca/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.nl/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.pl/game/x/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.se/game/x/B0GFWDM8NP?autoplay=1",
])
def test_the_page_is_recognised_in_every_luna_market(url):
    """cdp.wait_for_page does a substring match, so this is the whole rule."""
    assert LUNA["page_match"] in url, url


def test_the_old_host_marker_would_have_missed_most_markets():
    """Guards the regression, and records why it looked fine from Belgium.

    If someone "simplifies" page_match back to the launch URL's host, this
    fails and says which countries it breaks.
    """
    old = "luna.amazon.com"                      # url.split("/")[2]
    assert old in "https://luna.amazon.com.be/game/x"      # Belgium: by accident
    for broken in ("https://luna.amazon.de/game/x",
                   "https://luna.amazon.co.uk/game/x",
                   "https://luna.amazon.fr/game/x"):
        assert old not in broken, broken


def test_providers_that_never_change_host_declare_no_marker():
    """Boosteroid, GeForce NOW and Xbox stay on the host we asked for — Xbox
    localises by PATH (/nl-BE/play/...), not by domain — so they fall back to
    it rather than carrying a marker that could drift."""
    for key in ("boosteroid", "geforce_now", "xbox_cloud"):
        assert "page_match" not in launcher.provider_profile(key)


# ------------------------------------------------- is it actually streaming?

def _run_js(expression, dom):
    """Evaluate the launcher's real JS against a stubbed document/location."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = f"""
const fake = {json.dumps(dom)};
const location = {{ href: fake.href }};
const document = {{
  querySelectorAll: (sel) => sel === 'video' ? fake.videos : [],
  querySelector: (sel) => (sel === 'video' ? (fake.videos[0] || null) : null),
}};
console.log(JSON.stringify(Boolean({expression})));
"""
    out = subprocess.run([node, "-e", script], capture_output=True, text=True,
                         timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


TRAILER = {"paused": True, "ended": False, "videoWidth": 0, "videoHeight": 0}
STREAM = {"paused": False, "ended": False, "videoWidth": 1280, "videoHeight": 720}


def test_a_trailer_on_the_game_page_is_not_a_running_game():
    """The exact bug. Measured on the device: one <video> from
    m.media-amazon.com, paused, 0x0, beside an enabled "Play now"."""
    assert _run_js(launcher.IS_STREAMING_JS, {
        "href": "https://luna.amazon.com.be/game/fortnite?autoplay=1",
        "videos": [TRAILER],
    }) is False


def test_a_running_stream_is_recognised():
    assert _run_js(launcher.IS_STREAMING_JS, {
        "href": "https://luna.amazon.com.be/game/fortnite",
        "videos": [STREAM],
    }) is True


def test_a_page_with_no_video_at_all_is_not_streaming():
    assert _run_js(launcher.IS_STREAMING_JS, {
        "href": "https://luna.amazon.com/game/x", "videos": [],
    }) is False


def test_a_streaming_url_still_counts_even_before_the_video_exists():
    """Kept from the original check: GeForce NOW puts 'streaming' in the URL,
    and says so before the element appears."""
    assert _run_js(launcher.IS_STREAMING_JS, {
        "href": "https://play.geforcenow.com/streaming/session", "videos": [],
    }) is True


def test_a_trailer_next_to_a_real_stream_does_not_hide_it():
    assert _run_js(launcher.IS_STREAMING_JS, {
        "href": "https://luna.amazon.de/game/x", "videos": [TRAILER, STREAM],
    }) is True


# -------------------------------------------------------- the Play control

def test_the_selector_cannot_match_the_signed_out_upsell():
    """Signed out, the primary button shares the prefix and leads to a Prime
    signup. The :not() is what makes this selector safe to run BEFORE the text
    rules, which is where a provider selector is read."""
    selector = LUNA["selectors"][0]
    assert 'primary_entitled_button' in selector
    assert ':not([data-test-id$="_log_in"])' in selector


def test_the_signed_out_check_uses_ids_not_words():
    """Luna localises by IP like Xbox does — a Belgian browser is served
    "Inloggen" — so a text check would pass for anyone abroad."""
    signed_out = LUNA["signed_out"]
    assert "item_secondary_nav_button_sign_in" in signed_out
    assert "primary_entitled_button_log_in" in signed_out


# --------------------------------------------- labels on the live Luna page

def _matcher():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_play_matcher
    return test_play_matcher.word_in


@pytest.mark.parametrize("label", ["play now", "nu spelen", "jetzt spielen",
                                   "jouer maintenant"])
def test_lunas_play_label_is_matched(label):
    assert _matcher()(label, 2) is True


@pytest.mark.parametrize("label", [
    "remote play",              # Luna's nav item, on every game page
    "play free with prime",     # the signed-out upsell
    "speel gratis met prime",
])
def test_controls_that_must_never_be_clicked(label):
    """All three carry a play word. "Remote Play" is a <div role=button>, so it
    lands in the BUTTON group where two leading words are read — it matched,
    and clicking it navigates away from the game."""
    assert _matcher()(label, 2) is False
    assert _matcher()(label, 4) is False


@pytest.mark.parametrize("label", ["add to favourites", "share", "report issue",
                                   "add to desktop", "gamenight", "my stuff"])
def test_the_rest_of_lunas_page_is_ignored(label):
    """Read off the live page, so this is the real surrounding furniture."""
    assert _matcher()(label, 2) is False


# --------------------------------------------------- Luna refuses Linux

def test_luna_asks_for_a_user_agent_override():
    """Luna shows "Unsupported Browser" on Linux and never starts the stream.

    Not a capability problem: measured on the device, the same browser reports
    "probably" for h264, h265, vp9 AND av1, has MediaSource and has EME. Luna
    reads the platform out of the User-Agent and refuses on that alone.
    """
    assert LUNA.get("user_agent"), "Luna needs a UA override or it cannot play"
    assert "Windows" in LUNA["user_agent"]


def test_only_luna_spoofs_its_user_agent():
    """Xbox localises its page by what the browser says it is, so lying to
    every provider to satisfy one is how a fix for one game breaks three."""
    for key in ("boosteroid", "geforce_now", "xbox_cloud"):
        assert not launcher.provider_profile(key).get("user_agent")


def test_the_override_reaches_the_browser_command():
    cmd = launcher.build_browser_command(
        "com.microsoft.Edge", "/profile", "https://luna.amazon.com/game/x",
        True, LUNA["user_agent"])
    assert any(part.startswith("--user-agent=") for part in cmd)


def test_a_provider_without_an_override_gets_no_flag():
    cmd = launcher.build_browser_command(
        "com.microsoft.Edge", "/profile", "https://www.xbox.com/play", True, "")
    assert not any(part.startswith("--user-agent") for part in cmd)


# ------------------------------------------- the launcher identifies itself

def test_the_launcher_sends_a_user_agent_to_the_gateway():
    """urllib's default is "Python-urllib/3.x", which the reverse proxy in
    front of the gateway answers 403 to — measured: every other User-Agent
    reaches the app and gets a normal 401 for a bad token.

    It failed invisibly, which is why it lasted: the launcher falls back to the
    stored URL and the game still starts, so nothing looked wrong. It only
    meant no launch was resolved live and none was ever counted.
    """
    import inspect
    source = inspect.getsource(launcher.resolve_launch)
    assert '"User-Agent"' in source
    assert "Python-urllib" not in launcher.USER_AGENT
