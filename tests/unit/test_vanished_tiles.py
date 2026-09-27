# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""A tile deleted from Steam behind the plugin's back is tracked forever.

A shortcut removed from shortcuts.vdf stays in synced.json, so every sync treats
it as present: no new tile is made and reconcile rewrites a shortcut that is not
there. The game leaves the library and no sync brings it back. This forgets a
tracked tile once Steam no longer has it.
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


def _plugin(tracked):
    plugin = main.Plugin.__new__(main.Plugin)
    plugin._synced = dict(tracked)
    plugin._vanish_suspects = set()
    plugin._save_state = mock.AsyncMock()
    return plugin


TRACKED = {
    "570:boosteroid": {"app_id": 2188041668, "name": "Decimated (Boosteroid)"},
    "12:geforce_now": {"app_id": 4001205961, "name": "ACE COMBAT 7 (GeForce NOW)"},
}


def _run(plugin, steam_has):
    with mock.patch.object(main.shortcuts, "shelfcloud_shortcuts",
                           lambda: [{"app_id": a, "game_id": 0, "provider": "",
                                     "name": ""} for a in steam_has]):
        return asyncio.run(plugin._forget_vanished_tiles())


def test_a_tile_steam_no_longer_has_is_forgotten():
    plugin = _plugin(TRACKED)
    # Steam has only the second one. The first pass suspects, the second acts.
    assert _run(plugin, [4001205961]) == 0
    assert _run(plugin, [4001205961]) == 1
    assert "570:boosteroid" not in plugin._synced
    assert "12:geforce_now" in plugin._synced


def test_one_pass_is_never_enough():
    """Steam flushes shortcuts.vdf when it feels like it, so a tile made moments
    ago can be absent from the copy on disk. Forgetting it there makes the next
    sync build a second tile — the duplicate disaster from the other side."""
    plugin = _plugin(TRACKED)
    assert _run(plugin, [4001205961]) == 0
    assert "570:boosteroid" in plugin._synced, "one missed pass must not be enough"
    plugin._save_state.assert_not_awaited()


def test_a_tile_that_comes_back_is_forgiven():
    """The flush landed between the two passes, which is the whole point."""
    plugin = _plugin(TRACKED)
    _run(plugin, [4001205961])                      # missing: suspected
    _run(plugin, [2188041668, 4001205961])          # back: cleared
    _run(plugin, [2188041668, 4001205961])
    assert "570:boosteroid" in plugin._synced


def test_the_state_file_is_written_when_something_went():
    plugin = _plugin(TRACKED)
    _run(plugin, [4001205961])
    _run(plugin, [4001205961])
    plugin._save_state.assert_awaited()


def test_nothing_happens_when_every_tile_is_still_there():
    plugin = _plugin(TRACKED)
    assert _run(plugin, [2188041668, 4001205961]) == 0
    assert len(plugin._synced) == 2
    plugin._save_state.assert_not_awaited()


def test_an_unreadable_shortcuts_file_forgets_nothing():
    """The guard that matters most. An empty read means the file could not be
    parsed, not that Steam has no shortcuts. Acting on it would forget every
    tile and rebuild the whole library under new app ids."""
    plugin = _plugin(TRACKED)
    assert _run(plugin, []) == 0
    assert len(plugin._synced) == 2


def test_a_raising_shortcuts_read_forgets_nothing():
    plugin = _plugin(TRACKED)

    def boom():
        raise OSError("no such file")

    with mock.patch.object(main.shortcuts, "shelfcloud_shortcuts", boom):
        assert asyncio.run(plugin._forget_vanished_tiles()) == 0
    assert len(plugin._synced) == 2


def test_an_empty_record_is_not_an_error():
    plugin = _plugin({})
    assert _run(plugin, [123]) == 0


def test_a_malformed_entry_does_not_raise():
    plugin = _plugin({"7:boosteroid": "not a dict"})
    assert _run(plugin, [4001205961]) == 0
    assert plugin._synced == {"7:boosteroid": "not a dict"}
