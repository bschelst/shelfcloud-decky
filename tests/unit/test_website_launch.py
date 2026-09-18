# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Website tiles: where they may go, and how the window differs from a game's.

A game tile is held to a five-host allowlist because it opens a kiosk-mode
Chromium — no address bar — on a profile holding live Microsoft, NVIDIA,
Boosteroid and Luna sessions. A page somebody added cannot be held to that
list: going somewhere the catalogue never heard of is the entire feature.

What makes the relaxation acceptable is the window, so that is tested here as
carefully as the URL rule is. A page gets its own profile, no debugging port
and no kiosk. If any one of those three quietly came back, the allowlist would
be protecting nothing and no URL test would notice.

Run: python3 -m pytest tests/unit
"""

import importlib.util
import os
import sys
import types

import pytest

sys.modules.setdefault("cdp", types.ModuleType("cdp"))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

spec = importlib.util.spec_from_file_location(
    "shelfcloud_launch", os.path.join(ROOT, "bin", "shelfcloud-launch.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

allowed = launcher.is_allowed_website_url


# --------------------------------------------------------- ordinary pages

@pytest.mark.parametrize("url", [
    "https://www.protondb.com/",
    "https://en.wikipedia.org/wiki/Steam_Deck",
    "http://example.org/plain",                    # http is permitted for pages
    "https://example.com:8443/app",
    "https://sub.domain.example.co.uk/deep/path?q=1#frag",
])
def test_a_public_page_is_allowed(url):
    """The point of the feature. A tile that could only reach five hosts would
    be the provider allowlist with extra steps."""
    assert allowed(url) is True


# ------------------------------------------------------- the user's network

@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://localhost:8080/",
    "http://192.168.1.1/",
    "http://10.0.0.5/",
    "http://172.16.4.3/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://[fd00::1]/",
    "http://0.0.0.0/",
    "http://printer.local/",
    "http://nas.internal/status",
    "http://router/",
])
def test_the_users_own_network_is_out_of_reach(url):
    """The tile is opened by the person's own browser, inside their own
    network, with their own cookies. That is not a website, it is a request to
    their router — and it would be made by whatever the gateway said."""
    assert allowed(url) is False


@pytest.mark.parametrize("url", [
    "http://0x7f.0.0.1/",
    "http://0177.0.0.1/",
    "http://127.1/",
    "http://2130706433/",
])
def test_numeric_spellings_of_a_local_address_are_refused(url):
    """Chromium resolves every one of these to 127.0.0.1."""
    assert allowed(url) is False


# ------------------------------------------------------------- the classics

@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "ftp://example.com/x",
    "steam://run/440",
    "//example.com/protocol-relative",
    "", None, 42, b"https://example.com",
])
def test_only_http_and_https(url):
    assert allowed(url) is False


@pytest.mark.parametrize("url", [
    "https://example.com@evil.example/",
    "https://user:pass@evil.example/",
])
def test_credentials_in_the_authority_are_refused(url):
    assert allowed(url) is False


@pytest.mark.parametrize("url", [
    "https://evil.example\\cloud.boosteroid.com/steal",
    "https://example.com\tX",
    "https://example.com\nLocation: x",
    "https://example.com\r\n",
])
def test_characters_a_browser_would_move_are_refused(url):
    """urlsplit reads a backslash as ordinary; Chromium ends the authority at
    it. When the two disagree about the host, the browser is the one that opens
    the page — so this is refused before either gets a say."""
    assert allowed(url) is False


# ------------------------------------------------- which gate applies to what

@pytest.mark.parametrize("provider", ["shelfcloud", "custom"])
def test_a_page_provider_uses_the_page_rule(provider):
    assert launcher.url_is_acceptable("https://www.protondb.com/", provider) is True
    assert launcher.url_is_acceptable("http://192.168.1.1/", provider) is False


@pytest.mark.parametrize("provider", ["boosteroid", "geforce_now", "xbox_cloud",
                                      "amazon_luna", ""])
def test_a_game_provider_still_uses_the_allowlist(provider):
    """The relaxation must not leak. A game is opened in a kiosk window on the
    profile with every provider session in it, and that window is still only
    allowed to reach the five hosts."""
    assert launcher.url_is_acceptable("https://www.protondb.com/", provider) is False
    assert launcher.url_is_acceptable("https://cloud.boosteroid.com/x", provider) is True


# ---------------------------------------------------------------- the window

def command(url="https://www.protondb.com/", profile="/home/deck/.shelfcloud/chrome"):
    return launcher.build_website_command("com.google.Chrome",
                                          launcher.website_profile(profile), url)


def test_a_page_never_gets_a_debugging_port():
    """A game window opens one so the launcher can press Play through CDP. It
    is unauthenticated and accepts commands from anything that reaches it,
    including script in the page being debugged — which is a trade worth making
    for four known provider sites and not for an arbitrary one."""
    assert not any("remote-debugging" in arg for arg in command())
    # And the game path still has it, so this test is about the page and not
    # about the flag having been dropped everywhere.
    game = launcher.build_browser_command("com.google.Chrome", "/tmp/p",
                                          "https://cloud.boosteroid.com/x")
    assert any("--remote-debugging-port" in arg for arg in game)


def test_a_page_is_not_opened_in_kiosk_mode():
    """Kiosk hides the address bar, and for a page the catalogue cannot vouch
    for, the address bar is the check — handed back to the person looking at
    it."""
    assert "--kiosk" not in command()


def test_a_page_cannot_reach_the_provider_sessions():
    """Cookies, storage and saved passwords live in --user-data-dir. A page
    sharing the game profile would be one XSS away from a live Microsoft
    session."""
    game_profile = "/home/deck/.shelfcloud/chrome"
    page_profile = launcher.website_profile(game_profile)
    assert page_profile != game_profile
    assert not page_profile.startswith(game_profile + "/")

    args = command(profile=game_profile)
    data_dir = next(a for a in args if a.startswith("--user-data-dir="))
    assert data_dir == f"--user-data-dir={page_profile}"
    assert game_profile not in data_dir.replace(page_profile, "")


def test_the_url_is_the_last_argument_and_is_not_a_flag():
    """Chromium reads a leading-dash argument as a flag. The URLs that could do
    that are refused above, but the ordering is what makes that true."""
    args = command()
    assert args[-1] == "https://www.protondb.com/"
    assert not args[-1].startswith("-")
