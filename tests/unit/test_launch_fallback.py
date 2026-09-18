# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The offline launch URL, now that it is no longer on the command line.

Steam shows launch options verbatim in a game's properties, so a provider deep
link sat there in the interface for every tile. A game tile now passes only
--game-id and --provider; the gateway resolves the URL at launch, and the
offline fallback is read from the plugin's own state file by game id.

Two callers still pass a URL positionally and must keep working: the website
tile, which has no game id to resolve from, and any shortcut written before the
change — those keep launching until the next sync rewrites them.

Run: python3 -m pytest tests/unit/test_launch_fallback.py
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
    """This file is read PRECISELY when the gateway cannot be asked to correct
    it, so it gets the same check as anything the gateway sends."""
    write, _ = state
    write({"321": {"url": stored}})
    assert launcher.stored_fallback("321") == ""


@pytest.mark.parametrize("content", ["", "{not json", "[]", "null", '"text"'])
def test_an_unreadable_state_file_degrades_to_no_fallback(state, content):
    """Runs on the path where the gateway has ALREADY failed. Raising here
    would turn a slow gateway into a launcher that crashes."""
    _write, path = state
    path.write_text(content, encoding="utf-8")
    assert launcher.stored_fallback("321") == ""


def test_a_missing_state_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "STATE_FILE", str(tmp_path / "nope.json"))
    assert launcher.stored_fallback("321") == ""


# --------------------------------------------------- the command line itself

def parse_argv(argv):
    """Mirror main()'s parsing: the flag PAIRS are removed from argv first, so
    their values never reach the positional list."""
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
    """An old shortcut carries its own URL; it is used as-is rather than being
    second-guessed, so behaviour does not change under people mid-upgrade."""
    write, _ = state
    write({"321": {"url": GOOD}})
    passed = "https://cloud.boosteroid.com/application/1573"
    assert (passed or launcher.stored_fallback("321")) == passed
