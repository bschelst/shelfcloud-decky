#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Where shortcut icons are written, and what uninstalling is allowed to delete.

Icons moved into Steam's own grid folder because one written anywhere else is
not drawn: the file landed in shortcuts.vdf correctly, as a valid PNG the deck
user could read, and Steam still rendered a broken image. Every other working
shortcut on the same device keeps its icon inside that folder.

That move makes two previously harmless things dangerous, and both are asserted
here rather than trusted:

  * `<appid>.png` in that folder is STEAM'S library art. Our game ids start at
    1, so an icon named after a game id can land on the artwork of a real Steam
    app with the same number and destroy it.
  * uninstall used to empty the icon directory. Emptying Steam's grid folder
    deletes the library artwork of every game on the machine.

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
    """The collision this prevents is silent and destroys somebody's artwork.

    In Steam's grid folder `23101.png` is the library art of Steam app 23101.
    Our game ids come from the same small range, so naming an icon after one
    overwrites a real game's picture with our mark.
    """
    assert main._icon_name(23101, 2250922664, "png") == "2250922664_icon.png"
    assert "23101" not in main._icon_name(23101, 2250922664, "png")


def test_without_an_app_id_it_falls_back_to_the_old_name():
    """Only so icons written by an earlier version can still be found and
    removed — nothing writes this shape any more."""
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
    """A grid folder holding Steam's artwork and one icon of ours."""
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


def test_uninstall_deletes_only_our_own_icons(grid, tmp_path):
    """The guard that matters. This used to be `for name in listdir: remove`,
    which in Steam's grid folder is every game's artwork on the device."""
    folder, steams = grid
    settings = tmp_path / "settings-icons"
    settings.mkdir()

    plugin = main.Plugin()
    plugin._synced = {"23101": {"game_id": 23101, "app_id": 2250922664}}

    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(settings)):
        asyncio.run(plugin._uninstall())

    assert not (folder / "2250922664_icon.png").exists(), "ours should be gone"
    for name, body in steams.items():
        assert (folder / name).read_bytes() == body, (
            f"{name} belongs to Steam or another shortcut and must survive")


def test_removing_one_tile_takes_only_that_tile_s_icon(grid, tmp_path):
    folder, steams = grid
    settings = tmp_path / "settings-icons"
    settings.mkdir()

    plugin = main.Plugin()
    plugin._synced = {"23101": {"game_id": 23101, "app_id": 2250922664}}
    plugin._save_state = mock.AsyncMock()

    with mock.patch.object(main, "_icon_dir", return_value=str(folder)), \
         mock.patch.object(main, "_settings_icon_dir", return_value=str(settings)):
        asyncio.run(plugin.forget_shortcut(23101))

    assert not (folder / "2250922664_icon.png").exists()
    assert (folder / "1234_icon.png").exists(), "another shortcut's icon"
    assert (folder / "570.png").exists()
