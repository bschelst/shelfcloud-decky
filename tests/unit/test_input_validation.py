# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""What the plugin accepts from the gateway, from disk, and from the frontend.

A game TITLE (scraped from a provider API) becomes a directory name, so it must
not escape the path. And an unreadable settings file must fail as one file, not
take the whole plugin down: _read_json has to return {} for every bad shape.
"""

import asyncio
import json
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


# ------------------------------------------------- a title becomes a path

def _install(name, tmp_path):
    """Try to install a layout under `name`, with a real Steam tree."""
    steam = tmp_path / "steam"
    cfg = steam / "steamapps" / "common" / "Steam Controller Configs" / "1" / "config"
    cfg.mkdir(parents=True, exist_ok=True)      # callable twice in one test
    template = tmp_path / "templates"
    template.mkdir(exist_ok=True)
    (template / "t.vdf").write_text('"controller_mappings"\n{\n}\n')

    plugin = main.Plugin.__new__(main.Plugin)
    with mock.patch.object(main.os.path, "expanduser", return_value=str(steam)), \
         mock.patch.object(main, "_TEMPLATE_DIR", str(template), create=True):
        result = asyncio.run(plugin.install_controller_layout(name, "template://t.vdf"))
    return result, cfg


def test_a_title_with_a_separator_is_refused(tmp_path):
    result, _ = _install("../../../../etc/evil", tmp_path)
    assert result["ok"] is False
    assert "unsafe" in result["error"]


def test_a_bare_dotdot_is_refused(tmp_path):
    assert _install("..", tmp_path)[0]["ok"] is False


def test_a_single_dot_is_refused(tmp_path):
    assert _install(".", tmp_path)[0]["ok"] is False


def test_nothing_is_written_outside_the_config_directory(tmp_path):
    """Not just refused, but nothing created. A harmless install runs first so
    the snapshot only sees what the escaping name did."""
    _install("Harmless Game (Boosteroid)", tmp_path)
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())

    _install("../../../../escaped", tmp_path)

    after = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    assert before == after
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path.parent / "escaped").exists()


def _install_for_real(name, tmp_path):
    """Like _install, but builds the tree the code really reads
    (<steam>/controller_base/templates/), so a file actually lands."""
    steam = tmp_path / "steam"
    cfg = steam / "steamapps" / "common" / "Steam Controller Configs" / "1" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    templates = steam / "controller_base" / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    (templates / "controller_neptune_gamepad+mouse.vdf").write_text(
        '"controller_mappings"\n{\n\t"controller_type"\t\t"controller_neptune"\n}\n')

    plugin = main.Plugin.__new__(main.Plugin)
    plugin._settings = {}          # extra pad types off, the standaard
    with mock.patch.object(main.os.path, "expanduser", return_value=str(steam)):
        result = asyncio.run(plugin.install_controller_layout(
            name, "template://controller_neptune_gamepad+mouse.vdf"))
    return result, cfg


def test_a_title_with_a_slash_nests_instead_of_being_refused(tmp_path):
    """"Disney/Pixar WALL-E" is a real game. Steam keys config by name and a
    slash just nests one level, so refusing every seperator broke real games.
    """
    result, cfg = _install_for_real("Disney/Pixar WALL-E (Amazon Luna)", tmp_path)
    assert "unsafe" not in str(result.get("error", "")), result

    written = list(cfg.rglob("controller_neptune.vdf"))
    assert written, "a legitimate slashed title got no layout at all"
    # nested, but still inside the per-user config dir
    for path in written:
        assert str(path.resolve()).startswith(str(cfg.resolve()) + os.sep)


def test_a_slash_still_cannot_be_used_to_climb(tmp_path):
    """Allowing the separator must not re-open the hole it was closing."""
    for bad in ("Disney/../../../x", "foo/../../bar", "/etc/passwd",
                "a//b", "./../x"):
        result, _ = _install(bad, tmp_path)
        assert result["ok"] is False, f"{bad!r} was accepted"
    assert not (tmp_path.parent / "x").exists()
    assert not (tmp_path / "x").exists()


def test_an_ordinary_game_title_is_still_accepted(tmp_path):
    # real titles have colons, apostrophes and brackets
    result, _ = _install("Tom Clancy's The Division 2 (Xbox Cloud)", tmp_path)
    assert result["ok"] is not False or "unsafe" not in str(result.get("error", ""))


def test_an_empty_or_non_string_title_is_refused(tmp_path):
    for bad in ("", "   ", None, 42, [], {}):
        plugin = main.Plugin.__new__(main.Plugin)
        result = asyncio.run(plugin.install_controller_layout(bad, "template://t.vdf"))
        assert result["ok"] is False, f"{bad!r} was accepted"


def test_a_non_string_template_url_is_refused():
    plugin = main.Plugin.__new__(main.Plugin)
    for bad in (None, 7, [], {}):
        result = asyncio.run(plugin.install_controller_layout("A Game", bad))
        assert result["ok"] is False


# ------------------------------------------------------ reading state files

def test_a_missing_file_reads_as_empty(tmp_path):
    assert main.Plugin._read_json(str(tmp_path / "nope.json")) == {}


def test_a_json_array_does_not_become_the_settings(tmp_path):
    """`[]` is valid JSON but not a mapping. Returned as-is, self._settings
    became a list and every .get() after it raised."""
    path = tmp_path / "settings.json"
    path.write_text("[]")
    assert main.Plugin._read_json(str(path)) == {}


def test_other_json_scalars_are_refused_too(tmp_path):
    for content in ("null", '"a string"', "42", "true"):
        path = tmp_path / "s.json"
        path.write_text(content)
        assert main.Plugin._read_json(str(path)) == {}, content


def test_broken_json_reads_as_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json")
    assert main.Plugin._read_json(str(path)) == {}


def test_invalid_utf8_reads_as_empty_rather_than_raising(tmp_path):
    """UnicodeDecodeError is not a JSONDecodeError, so an uncaught corrupt byte
    stopped the plugin loading."""
    path = tmp_path / "s.json"
    path.write_bytes(b'{"browser": "\xff\xfe bad"}')
    assert main.Plugin._read_json(str(path)) == {}


def test_a_directory_reads_as_empty(tmp_path):
    folder = tmp_path / "adir.json"
    folder.mkdir()
    assert main.Plugin._read_json(str(folder)) == {}


def test_a_real_object_still_loads(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"browser": "com.microsoft.Edge", "sync_minutes": 3}))
    assert main.Plugin._read_json(str(path))["sync_minutes"] == 3
