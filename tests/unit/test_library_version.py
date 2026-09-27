# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""An unchanged library is not sent across the socket again.

get_desired_library can be 1.6 MB and crossed the websocket every sync just to
report no change. Now the reply carries a version the caller hands back, and an
unchanged answer is a few hundred bytes. The version covers only what the caller
acts on (desired tiles + tracking), so a new field cannot stop it matching.
"""

import asyncio
import sys
from unittest import mock

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main

GAME = {
    "id": 1,
    "name": "Valheim",
    "chosen_provider": "boosteroid",
    "providers": [{"provider": "boosteroid", "needs_tier": None,
                   "launch_url": "https://cloud.boosteroid.com/play/valheim"}],
}
BROWSER = {"app_id": "com.google.Chrome", "label": "Google Chrome"}


def plugin(tmp_path, games=(GAME,)):
    main.decky.DECKY_PLUGIN_SETTINGS_DIR = str(tmp_path)
    p = main.Plugin()
    p._settings = {"api_token": "t", "browser": BROWSER["app_id"]}
    p._synced = {}
    p._request = lambda *a, **k: {"games": list(games)}
    p._forget_vanished_tiles = mock.AsyncMock(return_value=0)
    return p


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def library(p, known=""):
    with mock.patch.object(main.browsers, "resolve_browser",
                           lambda _: dict(BROWSER)):
        return run(p.get_desired_library(known))


def test_the_first_answer_carries_the_whole_library_and_a_version(tmp_path):
    reply = library(plugin(tmp_path))
    assert reply["ok"] is True
    assert reply["desired"], "expected the tiles themselves"
    assert reply["version"], "expected a version to hand back"
    assert not reply.get("unchanged")


def test_handing_back_the_same_version_gets_the_short_answer(tmp_path):
    p = plugin(tmp_path)
    first = library(p)
    again = library(p, first["version"])

    assert again["unchanged"] is True
    assert again["version"] == first["version"]
    assert "desired" not in again, "the megabyte was sent again"
    assert "tracked" not in again


def test_a_changed_library_is_sent_in_full(tmp_path):
    p = plugin(tmp_path)
    first = library(p)

    other = dict(GAME, id=2, name="Subnautica")
    p._request = lambda *a, **k: {"games": [GAME, other]}
    second = library(p, first["version"])

    assert not second.get("unchanged")
    assert second["version"] != first["version"]
    assert len(second["desired"]) > len(first["desired"])


def test_a_changed_tracking_record_also_counts_as_changed(tmp_path):
    """`tracked` is half the version, so a newly recorded tile must not be
    witheld."""
    p = plugin(tmp_path)
    first = library(p)

    p._synced["1:boosteroid"] = {"app_id": 42, "name": "Valheim (Boosteroid)"}
    second = library(p, first["version"])

    assert not second.get("unchanged")
    assert second["tracked"]


def test_an_unknown_version_gets_the_whole_thing(tmp_path):
    reply = library(plugin(tmp_path), "not-a-real-version")
    assert not reply.get("unchanged")
    assert reply["desired"]


def test_no_version_at_all_gets_the_whole_thing(tmp_path):
    """An older frontend sends nothing, and must still work"""
    reply = library(plugin(tmp_path), "")
    assert not reply.get("unchanged")
    assert reply["desired"]


def test_the_version_is_stable_across_calls(tmp_path):
    """Otherwise it never matches and the saving is nil."""
    p = plugin(tmp_path)
    assert library(p)["version"] == library(p)["version"]
