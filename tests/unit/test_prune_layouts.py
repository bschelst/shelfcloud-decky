# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Removing layout directories an older naming rule left behind.

The name Steam reads was got wrong twice: first the shortcut's own case, then
lower case, and only then lower case with the characters Steam strips. Each
attempt wrote a directory and nothing removed the last one -- measured on a
real Deck, 552 dead directories holding 9.2 MB that Steam had never opened.

This deletes directories, so the guards matter more than the feature. It may
only remove a directory that is OURS, under a spelling an earlier rule
produced, and only when the name Steam reads today sits beside it.

Run: python3 -m pytest tests/unit/test_prune_layouts.py
"""

import os
import sys

import pytest

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main


@pytest.fixture
def deck(tmp_path, monkeypatch):
    """A Steam tree with one user directory, and a plugin pointed at it."""
    root = tmp_path / "steam"
    cfg = root / "steamapps" / "common" / "Steam Controller Configs" / "123" / "config"
    cfg.mkdir(parents=True)
    # expanduser, not realpath: the code resolves the symlink itself, and
    # patching realpath would also defeat the containment check the prune
    # relies on -- which is the guard most worth exercising for real.
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(root) if p.startswith("~/.steam") else p)
    plugin = main.Plugin()
    plugin._settings = {}
    plugin._synced = {}
    return plugin, cfg


def make(cfg, name, body="x"):
    d = cfg / name
    d.mkdir()
    (d / "controller_neptune.vdf").write_text(body)
    return d


def track(plugin, name):
    plugin._synced[name] = {"name": name, "app_id": 1}


def test_it_removes_the_old_spellings(deck):
    plugin, cfg = deck
    track(plugin, "R.E.P.O. (Boosteroid)")
    current = make(cfg, "repo (boosteroid)")
    old_case = make(cfg, "R.E.P.O. (Boosteroid)")
    lowered = make(cfg, "r.e.p.o. (boosteroid)")

    assert plugin._prune_stale_layouts() == 2
    assert current.is_dir(), "removed the one Steam actually reads"
    assert not old_case.exists()
    assert not lowered.exists()


def test_it_never_removes_the_only_copy(deck):
    """If the current spelling is not there, the old one is all the user has.

    Deleting it would take away a working layout to tidy up a name.
    """
    plugin, cfg = deck
    track(plugin, "R.E.P.O. (Boosteroid)")
    only = make(cfg, "R.E.P.O. (Boosteroid)")

    assert plugin._prune_stale_layouts() == 0
    assert only.is_dir()


def test_it_leaves_directories_that_are_not_ours(deck):
    """Steam's own, and layouts the user made by hand."""
    plugin, cfg = deck
    track(plugin, "Valheim (Boosteroid)")
    make(cfg, "valheim (boosteroid)")
    theirs = make(cfg, "Some Other Game")
    steam_owned = make(cfg, "boosteroid cloud gaming")

    plugin._prune_stale_layouts()
    assert theirs.is_dir()
    assert steam_owned.is_dir()


def test_a_name_that_needed_no_change_is_left_alone(deck):
    """Its current spelling IS its own name, so there is nothing stale."""
    plugin, cfg = deck
    track(plugin, "valheim (boosteroid)")
    d = make(cfg, "valheim (boosteroid)")

    assert plugin._prune_stale_layouts() == 0
    assert d.is_dir()


def test_nothing_tracked_removes_nothing(deck):
    plugin, cfg = deck
    d = make(cfg, "Anything At All")
    assert plugin._prune_stale_layouts() == 0
    assert d.is_dir()


def test_a_missing_configs_directory_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: str(tmp_path / "nope") if p.startswith("~/.steam") else p)
    plugin = main.Plugin()
    plugin._settings = {}
    plugin._synced = {"a": {"name": "A Game (Boosteroid)"}}
    assert plugin._prune_stale_layouts() == 0


def test_a_tracked_entry_without_a_name_is_skipped(deck):
    plugin, cfg = deck
    plugin._synced["broken"] = {"app_id": 1}
    plugin._synced["worse"] = None
    d = make(cfg, "Something")
    assert plugin._prune_stale_layouts() == 0
    assert d.is_dir()
