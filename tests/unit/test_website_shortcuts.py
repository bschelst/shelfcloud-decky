# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Which library entries become a Steam shortcut, now that some are pages.

_to_desired applies two rules, because there are two kinds of tile. A game is
https-only: its URL is written into a shortcut and opened in a window holding
live provider sessions. A page goes through weburl instead, which permits http
and refuses anything inside the user's network.

The two have to agree with the launcher. An entry that becomes a shortcut here
and is refused there is a tile that does nothing when pressed, with the reason
in a log file nobody opens — which is exactly how the website tile was broken
once before, when the gateway's own host was missing from the allowlist.

Run: python3 -m pytest tests/unit
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


def desired(game):
    plugin = main.Plugin()
    plugin._settings = {"api_token": "t", "browser": "chrome"}
    return plugin._to_desired(game)


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
    # The launcher is told which kind of tile this is; without that it would
    # apply the provider allowlist and refuse its own shortcut.
    assert "--provider" in made["options"] and provider in made["options"]


@pytest.mark.parametrize("url", [
    "http://192.168.1.1/admin",
    "http://127.0.0.1:8080/",
    "javascript:alert(1)",
    "https://user:pass@evil.example/",
    "https://example.com\\evil.example/",
])
def test_a_page_pointing_inside_the_network_never_becomes_a_shortcut(url):
    """Refused here as well as in the launcher. This value came off the network
    and gets persisted into a Steam shortcut, so the check that keeps it out of
    the file is separate from the one that keeps it out of the browser."""
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
    """The property that matters, stated directly: a page entry becomes a tile
    if and only if the launcher would open it. Both read weburl, so this holds
    by construction — and this test is what says so if one of them stops."""
    becomes_a_tile = desired(entry("custom", url)) is not None
    would_open = weburl.is_allowed_website_url(url)
    assert becomes_a_tile == would_open, url
