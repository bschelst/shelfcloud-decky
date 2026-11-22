# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""One tile per game was fine until a game could have three.

With sync_all_providers on, Fortnite is three tiles all carrying game id 42, and
synced.json was keyed by game id, so tiles overwrote each other into orphans.
The key is composite now whether or not the setting is on — a key that changes
shape when the toggle flips would orphan every tile at once.
"""

import os
import sys
import types
from unittest import mock

# Decky's runtime is not importable off-device; stub what main.py touches.
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

from main import Plugin, _key_game_id, _tracking_key  # noqa: E402


# ------------------------------------------------------------------- shape

def test_a_key_names_the_game_and_the_service():
    assert _tracking_key(42, "boosteroid") == "42:boosteroid"


def test_the_game_id_comes_back_out():
    assert _key_game_id("42:boosteroid") == 42


def test_an_old_bare_key_still_yields_its_game_id():
    assert _key_game_id("42") == 42


def test_a_key_that_is_not_one_yields_nothing():
    assert _key_game_id("nonsense") is None
    assert _key_game_id("") is None


def test_two_services_for_one_game_are_different_keys():
    assert _tracking_key(42, "boosteroid") != _tracking_key(42, "amazon_luna")


# --------------------------------------------------------------- migration

def test_an_old_record_is_re_keyed_by_its_stored_provider():
    out = Plugin._migrate_keys({"42": {"app_id": 7, "provider": "boosteroid"}})
    assert out == {"42:boosteroid": {"app_id": 7, "provider": "boosteroid"}}


def test_migration_runs_once_and_then_leaves_it_alone():
    once = Plugin._migrate_keys({"42": {"app_id": 7, "provider": "boosteroid"}})
    assert Plugin._migrate_keys(once) == once


def test_an_entry_with_no_provider_keeps_its_old_key():
    # Guessing one would point the tile at a service it does not launch, and a
    # tile that matches nothing desired is deleted — loosing its playtime.
    state = {"42": {"app_id": 7}}
    assert Plugin._migrate_keys(state) == state


def test_migration_never_loses_an_entry():
    state = {
        "1": {"app_id": 11, "provider": "geforce_now"},
        "2": {"app_id": 22, "provider": "boosteroid"},
        "3": {"app_id": 33},                       # no provider
        "4:amazon_luna": {"app_id": 44, "provider": "amazon_luna"},
    }
    out = Plugin._migrate_keys(state)
    assert len(out) == len(state)
    assert sorted(e["app_id"] for e in out.values()) == [11, 22, 33, 44]


def test_a_bare_leftover_of_a_tracked_tile_is_dropped():
    # 14 of these on a steammachine: an old build wrote the tile under its bare
    # id with nothing filled in, the composite record came later for the SAME
    # shortcut. The leftover counted it twice, as "Other 14" in the panel.
    twin = {"app_id": 2202998279, "name": "Medieval Dynasty (Xbox Cloud)",
            "provider": "xbox_cloud"}
    state = {"3082": {"app_id": 2202998279, "name": "", "launch": "", "art": "",
                      "provider": "", "url": ""},
             "3082:xbox_cloud": twin}
    assert Plugin._migrate_keys(state) == {"3082:xbox_cloud": twin}


def test_a_bare_entry_for_another_shortcut_stays():
    # different app id: a real second tile, not a leftover. Not ours to drop
    state = {"3082": {"app_id": 1}, "3082:xbox_cloud": {"app_id": 2, "provider": "xbox_cloud"}}
    assert Plugin._migrate_keys(state) == state


def test_rubbish_state_does_not_raise():
    assert Plugin._migrate_keys({}) == {}
    assert Plugin._migrate_keys(None) == {}
    assert Plugin._migrate_keys({"42": "not a dict"}) == {"42": "not a dict"}


# ------------------------------------------------------------ key resolving

def _plugin() -> Plugin:
    plugin = Plugin.__new__(Plugin)
    plugin._synced = {}
    return plugin


def test_a_frontend_sending_a_bare_id_gets_the_composite_key():
    plugin = _plugin()
    assert plugin._resolve_key(42, "boosteroid") == "42:boosteroid"


def test_the_superseded_old_entry_is_dropped_not_left_beside_the_new_one():
    plugin = _plugin()
    plugin._synced["42"] = {"app_id": 7, "provider": "boosteroid"}
    key = plugin._resolve_key(42, "boosteroid")
    assert key == "42:boosteroid"
    assert "42" not in plugin._synced, "the tile would be recorded twice"


def test_a_composite_key_is_passed_through_untouched():
    plugin = _plugin()
    assert plugin._resolve_key("42:amazon_luna", "amazon_luna") == "42:amazon_luna"


def test_without_a_provider_the_key_is_left_as_it_came():
    plugin = _plugin()
    assert plugin._resolve_key(42, "") == "42"
