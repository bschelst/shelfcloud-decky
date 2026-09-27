# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Losing our own state file must not throw away the user's Steam tiles.

Reconcile works from the tracked map, so an empty map reads as "no tiles ever
made" and every game gets a brand new tile while the old ones leak. Since a
shortcut's identity is its app id (playtime, collections, artwork, controller
config all hang off it), that abandons all of it. shortcuts.vdf is the authority
on what Steam has, and every tile carries "--game-id N", so state is rebuilt
from it.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, ROOT)


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    """The backend class, with its state file pointed at a temp dir."""
    sys.modules.setdefault("decky", _FakeDecky())
    import importlib
    main = importlib.import_module("main")
    monkeypatch.setattr(main, "_state_file", lambda: str(tmp_path / "synced.json"))
    return main


class _FakeDecky:
    """Decky is only used for logging here."""
    class logger:
        @staticmethod
        def warning(*a, **k): pass
        @staticmethod
        def info(*a, **k): pass
        @staticmethod
        def error(*a, **k): pass
    DECKY_PLUGIN_SETTINGS_DIR = "/tmp"
    DECKY_PLUGIN_RUNTIME_DIR = "/tmp"


TILES = [
    {"game_id": 1196, "app_id": 2206547112},
    {"game_id": 605, "app_id": 3985415159},
]


def test_a_good_state_file_is_used_as_is(plugin, monkeypatch, tmp_path):
    (tmp_path / "synced.json").write_text(json.dumps({"1196": {"app_id": 111}}))
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts",
                        lambda *a, **k: pytest.fail("must not need recovery"))
    assert plugin.Plugin._load_state() == {"1196": {"app_id": 111}}


def test_a_lost_state_file_recovers_the_real_app_ids(plugin, monkeypatch):
    """The whole point: the tiles already exist, so reconcile must see them as
    tracked and leave them alone."""
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: TILES)
    state = plugin.Plugin._load_state()
    assert state == {"1196": {"app_id": 2206547112},
                     "605": {"app_id": 3985415159}}


def test_an_unreadable_state_file_recovers_too(plugin, monkeypatch, tmp_path):
    """Corrupt, not just missing. _read_json answers {} to both."""
    (tmp_path / "synced.json").write_text("{not json")
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: TILES)
    assert len(plugin.Plugin._load_state()) == 2


def test_an_empty_state_file_recovers_too(plugin, monkeypatch, tmp_path):
    """The exact artefact found on the device: a backup containing "{}"."""
    (tmp_path / "synced.json").write_text("{}")
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: TILES)
    assert len(plugin.Plugin._load_state()) == 2


def test_first_run_stays_empty(plugin, monkeypatch):
    """No file and no tiles is the honest {} — nothing to recover, and nothing
    must be invented."""
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: [])
    assert plugin.Plugin._load_state() == {}


def test_tiles_the_user_deleted_stay_deleted(plugin, monkeypatch):
    """Recovery reads what Steam HAS, so a removed tile cannot come back."""
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts",
                        lambda *a, **k: [TILES[0]])
    assert list(plugin.Plugin._load_state()) == ["1196"]


def test_duplicates_recover_to_the_oldest(plugin, monkeypatch):
    """Steam stores oldest first, and the oldest tile is the one holding the
    playtime and the controller layout — same rule duplicates() documents."""
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", lambda *a, **k: [
        {"game_id": 1196, "app_id": 111},     # oldest
        {"game_id": 1196, "app_id": 222},
    ])
    assert plugin.Plugin._load_state() == {"1196": {"app_id": 111}}


def test_an_unreadable_shortcuts_file_never_blocks_startup(plugin, monkeypatch):
    def boom(*a, **k):
        raise OSError("vdf is gone")
    monkeypatch.setattr(plugin.shortcuts, "shelfcloud_shortcuts", boom)
    assert plugin.Plugin._load_state() == {}
