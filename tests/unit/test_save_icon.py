# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Writing shortcut icons to disk.

Icons are the one artwork that has to be a real file: Steam stores the icon in
shortcuts.vdf as a PATH and re-reads it whenever it draws. So the file must be
durable (not the plugin dir, which install overwrites) and written atomically,
since a half-written file is a broken icon rather than a missed one.
"""

import asyncio
import base64
import os
import sys
import types
from unittest import mock

import pytest

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


@pytest.fixture(autouse=True)
def _no_real_steam(monkeypatch):
    """Keep these tests off the developer's own Steam install.

    _icon_dir() finds Steam's grid folder by looking for a real shortcuts.vdf,
    so without this it would write into whatever install is present. Which
    folder is chosen is covered in test_icon_location.py.
    """
    monkeypatch.setattr(main.shortcuts, "shortcuts_files", lambda *a, **k: [])


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
    """The plugin dir is overwritten on every install, so an icon stored there
    would survive only until the next update and then every tile would lose it,
    with a dead path still recorded in shortcuts.vdf."""
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


# ------------------------------------------------- saving BY REFERENCE
#
# save_icon takes base64, so the frontend pushes the whole picture over Decky's
# websocket. aiohttp closes a socket on a message over 4 MB rather than raising,
# which silences the call: the promise never settles. It hit ordinary games,
# because the icon falls back to the 600x900 capsule. The backend already
# downloaded the image (it served the slices), so the icon is named, not shipped.

REAL_PNG = b"\x89PNG\r\n\x1a\n-pretend-this-is-an-image"


def test_saving_by_url_writes_the_file_without_being_handed_the_bytes(tmp_path):
    instance = plugin(tmp_path)
    with mock.patch.object(main.Plugin, "_fetch_image",
                           lambda self, url, headers: ("image/png", REAL_PNG)):
        result = asyncio.run(
            instance.save_icon_from_url(42, "https://cdn.example/x.png"))

    assert result["ok"] is True
    with open(result["path"], "rb") as handle:
        assert handle.read() == REAL_PNG


def test_saving_by_url_reuses_the_image_already_held(tmp_path):
    """fetch_artwork keeps the last image so its slices do not re-download it.
    The icon is usually that same image, so going to the CDN again would be a
    second download per game for nothing."""
    instance = plugin(tmp_path)
    url = "https://cdn.example/held.png"
    instance._artwork_part = (url, base64.b64encode(REAL_PNG).decode(), "png")

    def refuse(self, *args, **kwargs):
        raise AssertionError("re-downloaded an image it already held")

    with mock.patch.object(main.Plugin, "_fetch_image", refuse):
        result = asyncio.run(instance.save_icon_from_url(42, url))

    assert result["ok"] is True
    with open(result["path"], "rb") as handle:
        assert handle.read() == REAL_PNG


def test_a_held_image_for_a_different_url_is_not_used(tmp_path):
    """One entry, keyed by url. Writing the WRONG picture is worse than
    downloading the right one twice."""
    instance = plugin(tmp_path)
    instance._artwork_part = ("https://cdn.example/other.png",
                              base64.b64encode(b"not-this-one").decode(), "png")

    with mock.patch.object(main.Plugin, "_fetch_image",
                           lambda self, url, headers: ("image/png", REAL_PNG)):
        result = asyncio.run(
            instance.save_icon_from_url(42, "https://cdn.example/x.png"))

    with open(result["path"], "rb") as handle:
        assert handle.read() == REAL_PNG


def test_saving_by_url_refuses_plain_http(tmp_path):
    """Same rule as fetch_artwork: these bytes are written to disk and handed
    to Steam, so nothing on the path gets to swap them."""
    result = asyncio.run(
        plugin(tmp_path).save_icon_from_url(42, "http://cdn.example/x.png"))
    assert result["ok"] is False


def test_saving_by_url_refuses_something_that_is_not_an_image(tmp_path):
    with mock.patch.object(main.Plugin, "_fetch_image",
                           lambda self, url, headers: ("text/html", b"<html>")):
        result = asyncio.run(
            plugin(tmp_path).save_icon_from_url(42, "https://cdn.example/x"))
    assert result["ok"] is False


def test_a_download_that_fails_is_an_error_not_a_crash(tmp_path):
    def boom(self, url, headers):
        raise OSError("no route to host")

    with mock.patch.object(main.Plugin, "_fetch_image", boom):
        result = asyncio.run(
            plugin(tmp_path).save_icon_from_url(42, "https://cdn.example/x.png"))

    assert result["ok"] is False
    assert "no route" in result["error"]


def test_saving_by_url_names_the_file_after_the_shortcut(tmp_path):
    """The file lands in Steam's grid folder, where `<appid>.png` is already
    Steam's library art for the app with that id."""
    instance = plugin(tmp_path)
    with mock.patch.object(main.Plugin, "_fetch_image",
                           lambda self, url, headers: ("image/png", REAL_PNG)):
        result = asyncio.run(
            instance.save_icon_from_url(42, "https://cdn.example/x.png", 4242))

    assert "4242" in os.path.basename(result["path"])
