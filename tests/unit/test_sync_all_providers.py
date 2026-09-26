# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""One tile per service, for people who want to choose at launch time.

Off, a game gets the single route its preferred service gives it, which is what
almost everyone wants. On, it gets one tile per service it plays on, so it
turns up in each of those collections and the library says where a game can
actually be played rather than only where this device would have picked.

The two rules that matter and are easy to get wrong:

  - each tile has to carry its OWN provider on the command line, because all
    of them share a game id and the gateway would otherwise answer from its
    ranking and open the same service three times;
  - each tile needs its own tracking key, or the record keeps one of them and
    the rest become orphans nothing will ever clean up.

Run: python3 -m pytest tests/unit
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

import main  # noqa: E402

BROWSER = {"app_id": "com.google.Chrome", "label": "Google Chrome"}

FORTNITE = {
    "id": 42,
    "name": "Fortnite",
    "chosen_provider": "amazon_luna",
    "providers": [
        {"provider": "boosteroid",
         "launch_url": "https://cloud.boosteroid.com/play/fortnite"},
        {"provider": "amazon_luna",
         "launch_url": "https://luna.amazon.com/play/fortnite"},
        {"provider": "geforce_now",
         "launch_url": "https://play.geforcenow.com/games?game-id=fortnite"},
    ],
}


def desired(game, all_providers: bool):
    plugin = main.Plugin()
    plugin._settings = {"api_token": "t", "browser": BROWSER["app_id"],
                        "sync_all_providers": all_providers}
    with mock.patch.object(main.browsers, "resolve_browser", lambda _: dict(BROWSER)):
        return plugin._to_desired(game)


# ------------------------------------------------------------------- off

def test_off_by_default_one_game_is_one_tile():
    plugin = main.Plugin()
    plugin._settings = {"browser": BROWSER["app_id"]}
    assert plugin._sync_all_providers() is False


def test_off_it_is_the_preferred_service():
    made = desired(FORTNITE, all_providers=False)
    assert len(made) == 1
    assert made[0]["provider"] == "amazon_luna"


# -------------------------------------------------------------------- on

def test_on_every_service_becomes_a_tile():
    made = desired(FORTNITE, all_providers=True)
    assert [m["provider"] for m in made] == [
        "amazon_luna", "boosteroid", "geforce_now",
    ], "the preferred service first, then the rest in order"


def test_each_tile_gets_its_own_tracking_key():
    made = desired(FORTNITE, all_providers=True)
    keys = [m["key"] for m in made]
    assert len(set(keys)) == len(keys), "two tiles sharing a key orphans one"
    assert set(keys) == {"42:amazon_luna", "42:boosteroid", "42:geforce_now"}


def test_each_tile_launches_its_own_service():
    # The whole point. Without --provider on each one the gateway answers from
    # its own ranking and all three tiles open the same service.
    for made in desired(FORTNITE, all_providers=True):
        assert f"--provider {made['provider']}" in made["options"]


def test_every_tile_points_at_the_same_game():
    made = desired(FORTNITE, all_providers=True)
    assert {m["game_id"] for m in made} == {42}
    for m in made:
        assert "--game-id 42" in m["options"]


def test_each_tile_keeps_the_url_of_its_own_service():
    by_provider = {m["provider"]: m["url"] for m in desired(FORTNITE, True)}
    assert by_provider["boosteroid"].startswith("https://cloud.boosteroid.com")
    assert by_provider["amazon_luna"].startswith("https://luna.amazon.com")
    assert by_provider["geforce_now"].startswith("https://play.geforcenow.com")


def test_two_rows_for_one_service_make_one_tile():
    """The catalog holds more than one row for a game on a service.

    A regional entry, a re-release, a second storefront. Measured on a real
    account: 402 boosteroid rows across 316 games, and games listing six,
    seven and eight "providers" when only five exist.

    One tile per row gives them all the same tracking key, so they collide in
    synced.json — the sync makes the tile, makes it again for the next row,
    and the launch URL flips between the two.
    """
    twice = dict(FORTNITE, providers=[
        {"provider": "boosteroid",
         "launch_url": "https://cloud.boosteroid.com/play/fortnite"},
        {"provider": "boosteroid",
         "launch_url": "https://cloud.boosteroid.com/play/fortnite-eu"},
    ], chosen_provider="boosteroid")

    made = desired(twice, all_providers=True)
    assert len(made) == 1
    assert made[0]["key"] == "42:boosteroid"


def test_the_first_row_for_a_service_is_the_one_kept():
    twice = dict(FORTNITE, providers=[
        {"provider": "boosteroid", "launch_url": "https://cloud.boosteroid.com/first"},
        {"provider": "boosteroid", "launch_url": "https://cloud.boosteroid.com/second"},
    ], chosen_provider="boosteroid")

    assert desired(twice, True)[0]["url"].endswith("/first")


def test_duplicate_rows_never_produce_a_repeated_key():
    messy = dict(FORTNITE, providers=[
        {"provider": "geforce_now", "launch_url": "https://play.geforcenow.com/a"},
        {"provider": "boosteroid", "launch_url": "https://cloud.boosteroid.com/a"},
        {"provider": "geforce_now", "launch_url": "https://play.geforcenow.com/b"},
        {"provider": "boosteroid", "launch_url": "https://cloud.boosteroid.com/b"},
        {"provider": "amazon_luna", "launch_url": "https://luna.amazon.com/a"},
    ])
    keys = [m["key"] for m in desired(messy, all_providers=True)]
    assert len(keys) == len(set(keys)) == 3


def test_a_game_on_one_service_is_still_one_tile():
    single = dict(FORTNITE, providers=FORTNITE["providers"][:1],
                  chosen_provider="boosteroid")
    assert len(desired(single, all_providers=True)) == 1


def test_a_game_with_no_usable_route_makes_nothing():
    # http for a game is refused whether or not the setting is on.
    broken = dict(FORTNITE, providers=[
        {"provider": "boosteroid", "launch_url": "http://insecure.example/"}])
    assert desired(broken, all_providers=True) == []
    assert desired(broken, all_providers=False) == []


def test_the_setting_only_accepts_a_real_yes():
    # A stray truthy string from an older settings file must not silently
    # double the size of somebody's library.
    plugin = main.Plugin()
    plugin._settings = {"sync_all_providers": "no"}
    assert plugin._sync_all_providers() is False


# ------------------------------------------- routes behind a paywall
#
# Reported from a Deck, twice. Sonic Frontiers is free on Boosteroid and
# "Luna Premium" on Amazon; a Luna Prime account got BOTH tiles, and the Luna
# one opened on "Included in Luna Premium, start your 7-day free trial".
#
# Ranking had been taught about rungs by then and could not help: ranking
# chooses BETWEEN routes, and this mode keeps all of them. The gateway marks
# each route with the rung it still needs, and a route the subscription cannot
# reach is not a tile.

SONIC = {
    "id": 457,
    "name": "Sonic Frontiers",
    "chosen_provider": "boosteroid",
    "providers": [
        {"provider": "boosteroid", "needs_tier": None,
         "launch_url": "https://cloud.boosteroid.com/play/sonic"},
        {"provider": "amazon_luna", "needs_tier": "Luna Premium",
         "launch_url": "https://luna.amazon.com/play/sonic"},
    ],
}


def test_a_route_above_the_users_tier_gets_no_tile():
    made = desired(SONIC, all_providers=True)
    assert [m["provider"] for m in made] == ["boosteroid"], \
        "the Luna route needs a rung this account does not have"


def test_it_still_applies_with_one_tile_per_game():
    """Off, the single route must not be the unreachable one either."""
    assert [m["provider"] for m in desired(SONIC, all_providers=False)] == \
        ["boosteroid"]


def test_a_game_reachable_nowhere_still_keeps_a_tile():
    """Every route behind a paywall is still a game the user deliberately
    added. No tile at all looks like the sync lost it, so one is kept and the
    gateway's needs_tier is what says it will ask for money."""
    gated = dict(SONIC, chosen_provider="amazon_luna", providers=[
        {"provider": "amazon_luna", "needs_tier": "Luna Premium",
         "launch_url": "https://luna.amazon.com/play/sonic"},
    ])
    assert [m["provider"] for m in desired(gated, all_providers=True)] == \
        ["amazon_luna"]


def test_a_gateway_that_says_nothing_about_tiers_changes_nothing():
    """needs_tier is absent on an older gateway, and for anyone who has not
    said which tier they hold. Neither of those is a paywall."""
    assert len(desired(FORTNITE, all_providers=True)) == 3
