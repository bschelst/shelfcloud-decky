# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Which library entries become a Steam shortcut, now that some are pages.

_to_desired has two rules: a game is https-only, a page goes through weburl
(http allowed, the user's own network refused). Both must agree with the
launcher, or a tile becomes a shortcut here and is refused there — a dead tile
whose reason sits in a log nobody reads.
"""

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
import weburl  # noqa: E402


def entry(provider, url, name="A page", game_id=4242):
    return {
        "id": game_id,
        "name": name,
        "providers": [{"provider": provider, "launch_url": url, "storefront": "web"}],
        "chosen_provider": provider,
    }


# A browser, stubbed. _to_desired returns None when no supported browser is
# installed and detect_browsers() reads real flatpak dirs, so without this the
# file would pass or fail on wether the machine has Chrome. Detection has its
# own tests.
BROWSER = {"app_id": "com.google.Chrome", "label": "Google Chrome"}


def desired(game):
    plugin = main.Plugin()
    plugin._settings = {"api_token": "t", "browser": BROWSER["app_id"]}
    with mock.patch.object(main.browsers, "resolve_browser", lambda _: dict(BROWSER)):
        made = plugin._to_desired(game)
    # _to_desired answers with a list (one tile per service). Each case here
    # offers a single route, so the one entry is unwrapped
    return made[0] if made else None


# ------------------------------------------------------------------ pages

@pytest.mark.parametrize("provider", ["custom", "shelfcloud"])
@pytest.mark.parametrize("url", [
    "https://www.protondb.com/",
    "http://example.org/plain",          # http is fine for a page
])
def test_a_page_becomes_a_shortcut(provider, url):
    made = desired(entry(provider, url))
    assert made is not None, f"{provider} {url} produced no tile"
    assert made["provider"] == provider
    assert made["url"] == url
    # launcher is told which kind of tile this is, or it applies the provider
    # allowlist and refuses its own shortcut
    assert "--provider" in made["options"] and provider in made["options"]


@pytest.mark.parametrize("url", [
    "http://192.168.1.1/admin",
    "http://127.0.0.1:8080/",
    "javascript:alert(1)",
    "https://user:pass@evil.example/",
    "https://example.com\\evil.example/",
])
def test_a_page_pointing_inside_the_network_never_becomes_a_shortcut(url):
    """Refused here as well as in the launcher: the value comes off the network
    and is persisted into a shortcut, so keeping it out of the file is a
    separate check from keeping it out of the browser."""
    assert desired(entry("custom", url)) is None


# ------------------------------------------------------------------ games

@pytest.mark.parametrize("provider", ["boosteroid", "geforce_now", "xbox_cloud"])
def test_a_game_is_still_https_only(provider):
    """The relaxation is for pages. A game tile opens in the window with every
    provider session in it, and http there puts those on the wire."""
    assert desired(entry(provider, "http://cloud.boosteroid.com/x")) is None
    assert desired(entry(provider, "https://cloud.boosteroid.com/x")) is not None


def test_a_game_on_a_private_address_is_refused_by_the_https_rule():
    assert desired(entry("boosteroid", "http://192.168.1.1/")) is None


# -------------------------------------------------- the two ends agree

@pytest.mark.parametrize("url", [
    "https://www.protondb.com/",
    "http://example.org/plain",
    "http://192.168.1.1/admin",
    "http://127.0.0.1/",
    "javascript:alert(1)",
    "https://user:pass@evil.example/",
    "https://example.com\\evil.example/",
    "http://0x7f.0.0.1/",
])
def test_making_a_shortcut_and_opening_it_reach_the_same_verdict(url):
    """A page entry becomes a tile if and only if the launcher would open it.
    Both read weburl, so it holds by construction, and this test says so if one
    of them stops."""
    becomes_a_tile = desired(entry("custom", url)) is not None
    would_open = weburl.is_allowed_website_url(url)
    assert becomes_a_tile == would_open, url
