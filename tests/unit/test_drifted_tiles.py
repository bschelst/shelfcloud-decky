#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Tiles whose real Steam shortcut is not what the plugin recorded.

On a steammachine 130 tiles still ran the older Shelfcloud desktop app
(`flatpak run cc.shelfcloud.App launch --game-id 741 ...`), under a name without
the service. The plugin had taken them over and recorded its own launch, and as
sync compares the gateway only with that RECORD, it never rewrote them. Pressing
Play then did nothing. shortcuts.vdf is what Steam runs, so that is compared.
"""

import asyncio
import os
import struct
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
import shortcuts  # noqa: E402

OURS = ("/home/deck/homebrew/plugins/Shelfcloud/bin/shelfcloud-launch.py com.microsoft.Edge "
        "/p --game-id 741 --provider geforce_now")
OLD_APP = "run cc.shelfcloud.App launch --game-id 741 --via app --provider geforce_now"


def entry(index: int, app_id: int, name: str, exe: str, options: str) -> bytes:
    """One shortcut as Steam writes it in the binary vdf."""
    return (b"\x00" + str(index).encode() + b"\x00"
            + b"\x02appid\x00" + struct.pack("<I", app_id)
            + b"\x01AppName\x00" + name.encode() + b"\x00"
            + b"\x01Exe\x00" + exe.encode() + b"\x00"
            + b"\x01StartDir\x00/usr/bin/\x00"
            + b"\x01LaunchOptions\x00" + options.encode() + b"\x00"
            + b"\x08")


def test_the_reader_returns_exe_and_options():
    blob = b"\x00shortcuts\x00" + entry(0, 3049655096, "Against the Storm",
                                        "/usr/bin/flatpak", OLD_APP) + b"\x08\x08"
    [tile] = shortcuts.parse(blob)
    assert tile["exe"] == "/usr/bin/flatpak"
    assert tile["options"] == OLD_APP
    assert tile["game_id"] == 741 and tile["provider"] == "geforce_now"


def plugin_with(synced, steam):
    plugin = main.Plugin()
    plugin._synced = synced
    patch = mock.patch.object(main.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: steam)
    return plugin, patch


def test_a_tile_still_on_the_old_app_is_reported():
    plugin, patch = plugin_with(
        {"741:geforce_now": {"app_id": 3049655096, "name": "Against the Storm (GeForce NOW)",
                             "launch": OURS, "provider": "geforce_now"}},
        [{"app_id": 3049655096, "name": "Against the Storm", "exe": "/usr/bin/flatpak",
          "options": OLD_APP, "game_id": 741, "provider": "geforce_now"}])
    with patch:
        assert asyncio.run(plugin.tiles_drifted()) == {"ok": True, "app_ids": [3049655096]}


def test_a_tile_that_matches_its_record_is_not():
    plugin, patch = plugin_with(
        {"741:geforce_now": {"app_id": 7, "name": "Against the Storm (GeForce NOW)",
                             "launch": OURS}},
        [{"app_id": 7, "name": "Against the Storm (GeForce NOW)", "exe": "/usr/bin/python3",
          "options": OURS, "game_id": 741, "provider": "geforce_now"}])
    with patch:
        assert asyncio.run(plugin.tiles_drifted())["app_ids"] == []


def test_only_a_renamed_tile_is_reported_too():
    plugin, patch = plugin_with(
        {"1:boosteroid": {"app_id": 8, "name": "X (Boosteroid)", "launch": OURS}},
        [{"app_id": 8, "name": "X", "exe": "/usr/bin/python3", "options": OURS,
          "game_id": 1, "provider": "boosteroid"}])
    with patch:
        assert asyncio.run(plugin.tiles_drifted())["app_ids"] == [8]


def test_a_record_without_a_launch_is_left_to_the_normal_sync():
    # an empty record already reads as changed in sync, no need to say it twice
    plugin, patch = plugin_with(
        {"1": {"app_id": 9, "name": "", "launch": ""}},
        [{"app_id": 9, "name": "X", "exe": "/usr/bin/flatpak", "options": OLD_APP,
          "game_id": 1, "provider": ""}])
    with patch:
        assert asyncio.run(plugin.tiles_drifted())["app_ids"] == []


def test_a_tile_steam_does_not_have_is_not_reported():
    plugin, patch = plugin_with({"1:boosteroid": {"app_id": 10, "name": "X", "launch": OURS}}, [])
    with patch:
        assert asyncio.run(plugin.tiles_drifted())["app_ids"] == []


def test_an_unreadable_shortcuts_file_reports_nothing():
    plugin = main.Plugin()
    plugin._synced = {"1:boosteroid": {"app_id": 10, "name": "X", "launch": OURS}}

    def boom(*_a, **_k):
        raise OSError("locked")
    with mock.patch.object(main.shortcuts, "shelfcloud_shortcuts", boom):
        assert asyncio.run(plugin.tiles_drifted()) == {"ok": False, "app_ids": []}


def test_junk_records_do_not_break_it():
    plugin, patch = plugin_with({"a": None, "b": {"app_id": "x"}, "c": {}}, [])
    with patch:
        assert asyncio.run(plugin.tiles_drifted()) == {"ok": True, "app_ids": []}
