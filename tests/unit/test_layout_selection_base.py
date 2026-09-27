# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Which Steam user directory the layout selection points into.

Only the signed-in account's config dir may be used; anything else makes Steam
clear the selection to default://<appid>. An earlier "most entries wins" rule
picked the wrong account. The dir name is the low 32 bits of the Steam ID.
"""

import asyncio
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main

STEAM_ID = "76561198083341509"
ACCOUNT = "123075781"                        # the low 32 bits of it
OTHERS = ("0", "822541362", "1614735336")


@pytest.fixture
def deck(tmp_path, monkeypatch):
    """A Steam tree with four account directories, ours NOT the fullest."""
    root = tmp_path / "steam"
    configs = root / "steamapps" / "common" / "Steam Controller Configs"
    for account in (ACCOUNT,) + OTHERS:
        (configs / account / "config").mkdir(parents=True)
    # every other account gets more entries, so "fullest wins" picks wrong
    for index, account in enumerate(OTHERS):
        for n in range(5 + index):
            (configs / account / "config" / ("game %d" % n)).mkdir()
    (configs / ACCOUNT / "config" / "only one").mkdir()

    monkeypatch.setattr(os.path, "realpath", lambda p: str(root)
                        if "steam" in str(p) else os.path.abspath(p))
    instance = main.Plugin()
    instance._settings = {"steam_id64": STEAM_ID}
    return instance, str(configs)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_it_points_at_the_signed_in_account(deck):
    plugin, configs = deck
    result = run(plugin.layout_selection_base())

    assert result["ok"] is True, result
    assert result["base"] == os.path.join(configs, ACCOUNT, "config")


def test_the_fullest_directory_does_not_win(deck):
    """The bug: every other acount here holds more layouts than ours."""
    plugin, configs = deck
    base = run(plugin.layout_selection_base())["base"]

    for other in OTHERS:
        assert other not in base.split(os.sep), (
            "selected another account's directory; Steam would clear the "
            "selection to default://<appid> on every sync")


def test_it_falls_back_when_not_signed_in(deck):
    """No Steam ID stored yet. Still it has to answer with something usable."""
    plugin, _ = deck
    plugin._settings = {}
    result = run(plugin.layout_selection_base())

    assert result["ok"] is True
    assert os.path.isdir(result["base"])


def test_a_nonsense_steam_id_does_not_crash_it(deck):
    plugin, _ = deck
    plugin._settings = {"steam_id64": "not-a-number"}
    assert run(plugin.layout_selection_base())["ok"] is True


def test_it_reports_the_decks_own_pad(deck):
    plugin, _ = deck
    assert run(plugin.layout_selection_base())["native"] == "neptune"


def test_no_controller_configs_directory_is_an_error_not_a_crash(tmp_path,
                                                                 monkeypatch):
    monkeypatch.setattr(os.path, "realpath", lambda p: str(tmp_path / "nope"))
    instance = main.Plugin()
    instance._settings = {"steam_id64": STEAM_ID}
    result = run(instance.layout_selection_base())
    assert result["ok"] is False


def test_the_account_id_is_the_low_32_bits(deck):
    """Guards the constant itself: 76561198083341509 - 2^32 base = 123075781."""
    plugin, configs = deck
    with mock.patch.dict(plugin._settings, {"steam_id64": STEAM_ID}):
        base = run(plugin.layout_selection_base())["base"]
    assert os.path.basename(os.path.dirname(base)) == ACCOUNT
