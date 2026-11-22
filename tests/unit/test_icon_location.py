#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Where shortcut icons are written, and what uninstall may delete.

Icons live in Steam's grid folder, else Steam draws them broken. That makes two
things dangerous: `<appid>.png` there is Steam's own art, so an icon named after
a game id can overwrite it; and uninstall must not empty the whole folder.
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
sys.modules.setdefault("decky", decky)

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402

import pytest  # noqa: E402


# --------------------------------------------------------------- the name

def test_the_icon_is_named_after_the_shortcut_not_the_game():
    """`23101.png` is Steam app 23101's art; our ids share that range, so
    naming an icon after a game id would overwrite it."""
    assert main._icon_name(23101, 2250922664, "png") == "2250922664_icon.png"
    assert "23101" not in main._icon_name(23101, 2250922664, "png")


def test_without_an_app_id_it_falls_back_to_the_old_name():
    """Only so an older version's icons can still be found and removed."""
    assert main._icon_name(23101, None, "png") == "23101.png"


# ------------------------------------------------------- where it is written

def test_steam_own_grid_folder_is_preferred(tmp_path):
    user = tmp_path / "userdata" / "123" / "config"
    user.mkdir(parents=True)
    (user / "shortcuts.vdf").write_bytes(b"\x00")

    with mock.patch.object(main.shortcuts, "shortcuts_files",
                           return_value=[str(user / "shortcuts.vdf")]):
        assert main._icon_dir() == str(user / "grid")
        assert os.path.isdir(user / "grid"), "the folder is created if absent"


def test_it_falls_back_when_steam_cannot_be_found():
    """Better a durable icon Steam may not draw than no icon at all."""
    with mock.patch.object(main.shortcuts, "shortcuts_files", return_value=[]):
        with mock.patch.object(main, "_settings_icon_dir",
                               return_value="/settings/Shelfcloud/icons"):
            assert main._icon_dir() == "/settings/Shelfcloud/icons"


# ------------------------------------------------------ what uninstall deletes

@pytest.fixture
def grid(tmp_path):
    """A grid folder holding Steam's artwork and one icon of ours"""
    folder = tmp_path / "grid"
    folder.mkdir()
    steams = {
        "570.png": b"dota header",
        "570p.png": b"dota capsule",
        "570_hero.png": b"dota hero",
        "1234_icon.png": b"somebody else's shortcut icon",
    }
    for name, body in steams.items():
        (folder / name).write_bytes(body)
    (folder / "2250922664_icon.png").write_bytes(b"ours")
    return folder, steams


def test_uninstall_leaves_every_icon_in_the_grid(grid, tmp_path):
    """Decky calls _uninstall on every UPDATE too, store updates included: it
    uninstalls the old version before it puts the new one. The tiles stay in
    the library through that, so deleting their icons left 377 of them pointing
    at nothing after the 0.3.7 update (Fortnite on GeForce NOW, grey circle).
    Our icons stay, and Steam's own art of course."""
    folder, steams = grid
    settings = tmp_path / "settings-icons"
    settings.mkdir()

    plugin = main.Plugin()
    plugin._synced = {"23101:shelfcloud": {"app_id": 2250922664, "name": "X",
                                           "launch": "", "art": "", "provider": "shelfcloud"}}

    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(settings)):
        asyncio.run(plugin._uninstall())

    assert (folder / "2250922664_icon.png").read_bytes() == b"ours"
    for name, body in steams.items():
        assert (folder / name).read_bytes() == body


def test_uninstall_still_clears_the_old_settings_folder(grid, tmp_path):
    """That one really was ours alone, nothing in Steam points into it anymore."""
    folder, _ = grid
    settings = tmp_path / "settings-icons"
    settings.mkdir()
    (settings / "23101.png").write_bytes(b"old icon")

    plugin = main.Plugin()
    plugin._synced = {}
    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(settings)):
        asyncio.run(plugin._uninstall())

    assert not settings.exists()


# ------------------------------------------------- tiles that lost their icon

def test_tiles_missing_an_icon_are_named(grid):
    folder, _ = grid
    plugin = main.Plugin()
    plugin._synced = {
        "2:geforce_now": {"app_id": 2250922664, "provider": "geforce_now"},   # has one
        "2:boosteroid": {"app_id": 4053800932, "provider": "boosteroid"},      # lost it
        "9:xbox_cloud": {"app_id": 1111, "provider": "xbox_cloud"},            # lost it
    }
    with mock.patch.object(main, "_icon_dir", return_value=str(folder)):
        answer = asyncio.run(plugin.tiles_missing_icons())
    assert answer == {"ok": True, "app_ids": [1111, 4053800932]}


def test_a_jpg_icon_counts_too(grid):
    folder, _ = grid
    (folder / "4053800932_icon.jpg").write_bytes(b"jpg icon")
    plugin = main.Plugin()
    plugin._synced = {"2:boosteroid": {"app_id": 4053800932, "provider": "boosteroid"}}
    with mock.patch.object(main, "_icon_dir", return_value=str(folder)):
        assert asyncio.run(plugin.tiles_missing_icons())["app_ids"] == []


def test_junk_entries_do_not_break_the_check(grid):
    folder, _ = grid
    plugin = main.Plugin()
    plugin._synced = {"a": None, "b": {"app_id": "nope"}, "c": {}, "d": {"app_id": -5}}
    with mock.patch.object(main, "_icon_dir", return_value=str(folder)):
        assert asyncio.run(plugin.tiles_missing_icons()) == {"ok": True, "app_ids": []}


def test_remove_icon_files_clears_both_naming_schemes(grid, tmp_path):
    """What the game id is for: an older icon under the game-id name, which
    only a real game id finds."""
    folder, _ = grid
    (folder / "23101.png").write_bytes(b"written by an older version")

    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(folder)):
        main.Plugin._remove_icon_files(23101, 2250922664)

    assert not (folder / "23101.png").exists(), "the legacy name survived"
    assert not (folder / "2250922664_icon.png").exists()
    assert (folder / "570.png").exists(), "Steam's own artwork is untouched"


def test_removing_one_tile_takes_only_that_tile_s_icon(grid, tmp_path):
    folder, steams = grid
    settings = tmp_path / "settings-icons"
    settings.mkdir()

    plugin = main.Plugin()
    plugin._synced = {"23101": {"app_id": 2250922664, "name": "X",
                                "launch": "", "art": "", "provider": "shelfcloud"}}
    plugin._save_state = mock.AsyncMock()

    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(settings)):
        asyncio.run(plugin.forget_shortcut(23101))

    assert not (folder / "2250922664_icon.png").exists()
    assert (folder / "1234_icon.png").exists(), "another shortcut's icon"
    assert (folder / "570.png").exists()
