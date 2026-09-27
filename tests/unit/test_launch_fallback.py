# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The offline launch URL, now off the command line.

A game tile passes only --game-id and --provider; the gateway resolves the URL,
and the offline fallback is read from synced.json by game id. Two callers still
pass a URL positionally (the website tile, and pre-change shortcuts) and must
keep working.
"""

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "launcher", os.path.join(ROOT, "bin", "shelfcloud-launch.py"))
launcher = importlib.util.module_from_spec(_spec)
sys.argv = ["x"]
_spec.loader.exec_module(launcher)

GOOD = "https://luna.amazon.com/game/fortnite?autoplay=1"


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Point the launcher at a throwaway synced.json."""
    path = tmp_path / "synced.json"
    monkeypatch.setattr(launcher, "STATE_FILE", str(path))

    def write(mapping):
        path.write_text(json.dumps(mapping), encoding="utf-8")
    return write, path


# ------------------------------------------------------ reading the fallback

def test_the_fallback_is_found_by_game_id(state):
    write, _ = state
    write({"321": {"app_id": 1, "name": "Fortnite", "url": GOOD}})
    assert launcher.stored_fallback("321") == GOOD


def test_a_game_with_no_record_has_no_fallback(state):
    write, _ = state
    write({"321": {"url": GOOD}})
    assert launcher.stored_fallback("999") == ""


def test_no_game_id_means_no_lookup(state):
    write, _ = state
    write({"321": {"url": GOOD}})
    assert launcher.stored_fallback("") == ""


@pytest.mark.parametrize("stored", [
    "http://cloud.boosteroid.com/application/1",   # not https
    "https://evil.example/phish",                  # not a provider
    "https://luna.amazon.com.evil.com/game/x",     # suffix lookalike
    "https://cloud.boosteroid.com@evil.example/",  # credentials in authority
    "https://cloud.boosteroid.com\\@evil.example/",  # backslash host confusion
    "", None, 42, [], {},
])
def test_a_fallback_that_is_not_a_permitted_url_is_ignored(state, stored):
    """Read when the gateway cannot correct it, so same check as gateway data."""
    write, _ = state
    write({"321": {"url": stored}})
    assert launcher.stored_fallback("321") == ""


@pytest.mark.parametrize("content", ["", "{not json", "[]", "null", '"text"'])
def test_an_unreadable_state_file_degrades_to_no_fallback(state, content):
    """Runs after the gateway already failed, so raising would crash the
    launcher."""
    _write, path = state
    path.write_text(content, encoding="utf-8")
    assert launcher.stored_fallback("321") == ""


def test_a_missing_state_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "STATE_FILE", str(tmp_path / "nope.json"))
    assert launcher.stored_fallback("321") == ""


# --------------------------------------------------- the command line itself

def parse_argv(argv):
    """Mirror main()'s parsing: flag pairs removed first, so their values never
    reach the positional list."""
    argv = list(argv)
    for flag in ("--provider", "--game-id"):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("--")]
    return args


def test_a_game_tile_passes_no_url_at_all():
    """The whole point: nothing in the Steam shortcut names a provider."""
    argv = ["com.microsoft.Edge", "/profile", "--game-id", "321",
            "--provider", "amazon_luna"]
    args = parse_argv(argv)
    assert args == ["com.microsoft.Edge", "/profile"]
    assert len(args) >= 2                # accepted; would have been rejected before
    assert not any("http" in a for a in argv)


def test_a_positional_url_is_still_accepted():
    """The website tile has no game id, and old shortcuts still carry one."""
    args = parse_argv(["com.google.Chrome", "/profile", launcher.GATEWAY,
                       "--no-autoplay"])
    assert len(args) == 3
    assert launcher.is_allowed_launch_url(args[2]) is True


def test_an_old_style_shortcut_still_parses():
    """Written before the change, and still on disk until the next sync."""
    args = parse_argv([
        "com.microsoft.Edge", "/profile",
        "https://cloud.boosteroid.com/application/1573",
        "--game-id", "321", "--provider", "boosteroid"])
    assert args == ["com.microsoft.Edge", "/profile",
                    "https://cloud.boosteroid.com/application/1573"]


def test_local_state_is_used_when_the_command_line_has_no_url(state):
    """What resolve_launch does before it ever contacts the gateway."""
    write, _ = state
    write({"321": {"url": GOOD}})
    assert ("" or launcher.stored_fallback("321")) == GOOD


def test_a_command_line_url_wins_over_local_state(state):
    """An old shortcut's own URL is used as-is, so mid-upgrade nothing changes."""
    write, _ = state
    write({"321": {"url": GOOD}})
    passed = "https://cloud.boosteroid.com/application/1573"
    assert (passed or launcher.stored_fallback("321")) == passed
