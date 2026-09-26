# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The provider badge: which tile is ours, and where the mark sits.

badge_position is written into a style on a patched Steam page, so it is
checked against the list rather than taken on trust — set_setting is an RPC
and the dropdown is not the only thing that can call it.

tracked_providers exists so the badge can ask one short question instead of
reading the whole library: it runs on a Steam route, once per game page
opened, which on a Deck is constantly.

Run: python3 -m pytest tests/unit
"""

import asyncio
import os
import sys
import types
from unittest import mock

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = "/plugins/Shelfcloud"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/settings/Shelfcloud"
decky.logger = mock.MagicMock()
decky.emit = mock.AsyncMock()
decky.DECKY_PLUGIN_VERSION = "0.0.0-test"
sys.modules["decky"] = decky

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402

TRACKED = {
    "1:boosteroid": {"app_id": 2672665986, "name": "RDR2 (Boosteroid)",
                     "provider": "boosteroid"},
    "42:amazon_luna": {"app_id": 111, "name": "Fortnite (Amazon Luna)",
                       "provider": "amazon_luna"},
    "7:geforce_now": {"app_id": 222, "provider": "geforce_now"},
}


def _plugin(tracked=None, settings=None):
    # `is None`, not `or`: an EMPTY library is a case worth testing, and
    # `tracked or TRACKED` would quietly hand it the full one instead.
    plugin = main.Plugin.__new__(main.Plugin)
    plugin._synced = dict(TRACKED if tracked is None else tracked)
    plugin._settings = dict(settings or {})
    plugin._save_settings = mock.AsyncMock()
    plugin.get_status = mock.AsyncMock(return_value={})
    return plugin


# -------------------------------------------------------- the app id map

def test_every_tracked_tile_is_named_by_its_app_id():
    out = asyncio.run(_plugin().tracked_providers())
    assert out["ok"] is True
    assert out["providers"] == {
        "2672665986": "boosteroid",
        "111": "amazon_luna",
        "222": "geforce_now",
    }


def test_the_keys_are_strings_because_they_cross_to_javascript():
    out = asyncio.run(_plugin().tracked_providers())
    assert all(isinstance(k, str) for k in out["providers"])


def test_an_entry_with_no_provider_is_left_out():
    # It would badge a tile with nothing to say, which is worse than no badge.
    out = asyncio.run(_plugin({"9:x": {"app_id": 5}}).tracked_providers())
    assert out["providers"] == {}


def test_a_malformed_entry_does_not_raise():
    messy = {"a": "not a dict", "b": {"app_id": "nope", "provider": "boosteroid"},
             "c": {"app_id": 3, "provider": 7}, "d": None}
    out = asyncio.run(_plugin(messy).tracked_providers())
    assert out["providers"] == {}


def test_an_empty_library_answers_empty_rather_than_failing():
    out = asyncio.run(_plugin({}).tracked_providers())
    # steam_ids rides alongside providers: the badge needs a game's Steam
    # appid to ask how many people are playing it, and a cloud tile is a
    # non-Steam shortcut so Steam cannot be asked about the tile itself.
    assert out == {"ok": True, "providers": {}, "steam_ids": {}}


# ----------------------------------------------------------- the position

def test_a_real_corner_is_stored():
    for corner in ("tl", "tr", "bl", "br"):
        plugin = _plugin()
        asyncio.run(plugin.set_setting("badge_position", corner))
        assert plugin._settings["badge_position"] == corner


def test_anything_else_is_refused():
    """It lands in a style on a page we do not own."""
    for bad in ("middle", "", None, 7, "tl; background:url(x)", ["bl"]):
        plugin = _plugin()
        asyncio.run(plugin.set_setting("badge_position", bad))
        assert "badge_position" not in plugin._settings, f"{bad!r} was stored"


def test_a_refused_position_does_not_save_the_file():
    plugin = _plugin()
    asyncio.run(plugin.set_setting("badge_position", "nowhere"))
    plugin._save_settings.assert_not_awaited()


def test_the_badge_can_be_switched_off():
    plugin = _plugin()
    asyncio.run(plugin.set_setting("badge_enabled", False))
    assert plugin._settings["badge_enabled"] is False


def test_the_card_badge_has_its_own_switch():
    """Two places, and wanting one without the other is ordinary.

    Turning the game-page badge off must not silently take the card badge
    with it, nor the other way round.
    """
    plugin = _plugin()
    asyncio.run(plugin.set_setting("badge_enabled", False))
    assert "badge_on_cards" not in plugin._settings

    asyncio.run(plugin.set_setting("badge_on_cards", False))
    assert plugin._settings["badge_on_cards"] is False
    assert plugin._settings["badge_enabled"] is False


def test_an_unknown_setting_is_still_refused():
    plugin = _plugin()
    asyncio.run(plugin.set_setting("badge_colour", "#ff0000"))
    assert "badge_colour" not in plugin._settings
