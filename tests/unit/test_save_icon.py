# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Writing shortcut icons to disk.

Icons are the one piece of artwork that has to become a real file. Steam takes
the four library slots as bytes and keeps its own copy; an icon it stores in
shortcuts.vdf as a PATH and re-reads whenever it draws the icon. Two
consequences drive everything here:

  - the file must be somewhere durable, NOT the plugin directory, which
    install-to-deck.sh overwrites on every install
  - a half-written file is a broken icon, not a missed one, because Steam may
    read it at any time -- so writes land atomically

Run: python3 -m pytest tests/unit
"""

import asyncio
import base64
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

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n-pretend-this-is-an-image").decode()


def plugin(tmp_path):
    main.decky.DECKY_PLUGIN_SETTINGS_DIR = str(tmp_path)
    instance = main.Plugin()
    instance._settings = {}
    instance._synced = {}
    return instance


def test_writes_the_image_and_returns_its_path(tmp_path):
    result = asyncio.run(plugin(tmp_path).save_icon(42, PNG, "png"))

    assert result["ok"] is True
    assert os.path.isfile(result["path"])
    with open(result["path"], "rb") as handle:
        assert handle.read() == base64.b64decode(PNG)


def test_the_icon_lives_outside_the_plugin_directory(tmp_path):
    """The plugin directory is overwritten on every install.

    An icon stored there would survive exactly until the next update, then
    every tile would silently lose its icon with the path still recorded in
    shortcuts.vdf pointing at a file that no longer exists.
    """
    result = asyncio.run(plugin(tmp_path).save_icon(42, PNG, "png"))
    assert main.decky.DECKY_PLUGIN_DIR not in result["path"]
    assert str(tmp_path) in result["path"]


def test_the_write_is_atomic(tmp_path):
    """Steam re-reads this file whenever it draws the icon, so it must never
    observe a partial one. Written to a temporary name, then moved."""
    instance = plugin(tmp_path)
    seen = []
    real_replace = os.replace

    def watch(src, dst):
        seen.append((os.path.basename(src), os.path.basename(dst)))
        return real_replace(src, dst)

    with mock.patch.object(main.os, "replace", watch):
        asyncio.run(instance.save_icon(42, PNG, "png"))

    assert seen, "expected the file to be moved into place, not written in place"
    assert seen[0][0].endswith(".part")
    assert seen[0][1] == "42.png"


def test_no_part_file_is_left_behind(tmp_path):
    instance = plugin(tmp_path)
    asyncio.run(instance.save_icon(42, PNG, "png"))
    leftovers = [n for n in os.listdir(main._icon_dir()) if n.endswith(".part")]
    assert leftovers == []


# ------------------------------------------------------------- rejections

def test_rejects_an_unsupported_kind(tmp_path):
    result = asyncio.run(plugin(tmp_path).save_icon(42, PNG, "svg"))
    assert result["ok"] is False


def test_rejects_data_that_is_not_base64(tmp_path):
    result = asyncio.run(plugin(tmp_path).save_icon(42, "not base64 at all!!", "png"))
    assert result["ok"] is False
    assert "bad image data" in result["error"]


def test_rejects_an_empty_image(tmp_path):
    result = asyncio.run(plugin(tmp_path).save_icon(42, "", "png"))
    assert result["ok"] is False


def test_rejects_an_oversized_image(tmp_path):
    huge = base64.b64encode(b"x" * (main.ARTWORK_MAX_BYTES + 1)).decode()
    result = asyncio.run(plugin(tmp_path).save_icon(42, huge, "png"))
    assert result["ok"] is False
    assert "too large" in result["error"]


def test_rejects_a_game_id_that_is_not_a_number(tmp_path):
    """The id becomes a filename, so it must never carry a path separator."""
    result = asyncio.run(plugin(tmp_path).save_icon("../../etc/passwd", PNG, "png"))
    assert result["ok"] is False


def test_a_numeric_string_id_is_accepted_and_normalised(tmp_path):
    result = asyncio.run(plugin(tmp_path).save_icon("42", PNG, "png"))
    assert result["ok"] is True
    assert os.path.basename(result["path"]) == "42.png"


# ---------------------------------------------------------------- cleanup

def test_forgetting_a_shortcut_removes_its_icon(tmp_path):
    """Otherwise these accumulate forever: one per game ever added, each one
    read by nothing once its tile is gone."""
    instance = plugin(tmp_path)
    saved = asyncio.run(instance.save_icon(42, PNG, "png"))
    assert os.path.isfile(saved["path"])

    instance._save_state = mock.AsyncMock()
    asyncio.run(instance.forget_shortcut(42))

    assert not os.path.exists(saved["path"])


def test_forgetting_a_shortcut_with_no_icon_is_not_an_error(tmp_path):
    instance = plugin(tmp_path)
    instance._save_state = mock.AsyncMock()
    assert asyncio.run(instance.forget_shortcut(999))["ok"] is True
