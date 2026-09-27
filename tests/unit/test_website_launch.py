# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Website tiles: where they may go, and how the window differs from a game's.

A game tile is held to a five-host allowlist since it opens a kiosk Chromium on
a profile with live provider sessions. A page cannot be held to that list — the
whole feature is going elsewhere. What makes that safe is the window, tested here
as carefully as the URL rule: a page gets its own profile, no debug port, no
kiosk. If any of those came back the allowlist would protect nothing.
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
    """The point of the feature. A tile stuck to five hosts is just the provider
    allowlist with extra steps."""
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
    """Opened by the person's own browser, in their own network, with their own
    cookies — a request to their router, made by whatever the gateway said."""
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
    """urlsplit treats a backslash as ordinary; Chromium ends the authority at
    it. When they disagree about the host, the browser wins — so refuse it
    first."""
    assert allowed(url) is False


# ------------------------------------------------- which gate applies to what

@pytest.mark.parametrize("provider", ["shelfcloud", "custom"])
def test_a_page_provider_uses_the_page_rule(provider):
    assert launcher.url_is_acceptable("https://www.protondb.com/", provider) is True
    assert launcher.url_is_acceptable("http://192.168.1.1/", provider) is False


@pytest.mark.parametrize("provider", ["boosteroid", "geforce_now", "xbox_cloud",
                                      "amazon_luna", ""])
def test_a_game_provider_still_uses_the_allowlist(provider):
    """The relaxation must not leak. A game opens in a kiosk window on the
    profile with every provider sesion, still limited to the five hosts."""
    assert launcher.url_is_acceptable("https://www.protondb.com/", provider) is False
    assert launcher.url_is_acceptable("https://cloud.boosteroid.com/x", provider) is True


# ---------------------------------------------------------------- the window

def command(url="https://www.protondb.com/", profile="/home/deck/.shelfcloud/chrome"):
    return launcher.build_website_command("com.google.Chrome",
                                          launcher.website_profile(profile), url)


def test_a_page_never_gets_a_debugging_port():
    """A game window opens one so the launcher can press Play through CDP. It is
    unauthenticated and takes commands from anything that reaches it — a trade
    worth making for known provider sites, not an arbitrary page."""
    assert not any("remote-debugging" in arg for arg in command())
    # game path still has it, so this is about the page, not the flag being
    # dropped everywhere
    game = launcher.build_browser_command("com.google.Chrome", "/tmp/p",
                                          "https://cloud.boosteroid.com/x")
    assert any("--remote-debugging-port" in arg for arg in game)


def test_a_page_is_not_opened_in_kiosk_mode():
    """Kiosk hides the address bar, and for a page the catalogue can not vouch
    for, the address bar is the check."""
    assert "--kiosk" not in command()


def test_a_page_cannot_reach_the_provider_sessions():
    """Cookies, storage and passwords live in --user-data-dir. A page sharing
    the game profile is one XSS from a live Microsoft session."""
    game_profile = "/home/deck/.shelfcloud/chrome"
    page_profile = launcher.website_profile(game_profile)
    assert page_profile != game_profile
    assert not page_profile.startswith(game_profile + "/")

    args = command(profile=game_profile)
    data_dir = next(a for a in args if a.startswith("--user-data-dir="))
    assert data_dir == f"--user-data-dir={page_profile}"
    assert game_profile not in data_dir.replace(page_profile, "")


def test_the_url_is_the_last_argument_and_is_not_a_flag():
    """Chromium reads a leading-dash argument as a flag. Such URLs are refused
    above, but the ordering is what backs that up"""
    args = command()
    assert args[-1] == "https://www.protondb.com/"
    assert not args[-1].startswith("-")
