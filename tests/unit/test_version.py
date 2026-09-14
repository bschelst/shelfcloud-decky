# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""The About box reports the installed version, or admits it does not know.

Read from package.json at runtime rather than kept as a constant in Python:
Decky shows the version from that same file, and a second copy here would
drift. An About box that states the wrong version with confidence is worse
than one that says "unknown".

Run: python3 -m pytest tests/unit
"""

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
sys.modules["decky"] = decky

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402


def set_plugin_dir(path):
    """Point the plugin at a directory.

    Set through `main.decky`, not the stub above: another test module in this
    suite installs its own decky stub, and whichever imported `main` first is
    the object `main` actually holds. Mutating the local stub would silently
    do nothing.
    """
    main.decky.DECKY_PLUGIN_DIR = str(path)


def plugin_with_manifest(tmp_path, contents):
    """A Plugin whose DECKY_PLUGIN_DIR holds the given package.json text."""
    if contents is not None:
        (tmp_path / "package.json").write_text(contents, encoding="utf-8")
    set_plugin_dir(tmp_path)
    return main.Plugin()


def test_reads_the_version_from_package_json(tmp_path):
    plugin = plugin_with_manifest(tmp_path, json.dumps({"version": "1.2.3"}))
    assert plugin._version() == "1.2.3"


def test_matches_the_real_manifest(tmp_path):
    """Guards against the field being renamed out from under this."""
    set_plugin_dir(ROOT)
    expected = json.load(open(os.path.join(ROOT, "package.json")))["version"]
    assert main.Plugin()._version() == expected


def test_missing_manifest_says_unknown(tmp_path):
    assert plugin_with_manifest(tmp_path, None)._version() == "unknown"


def test_malformed_manifest_says_unknown(tmp_path):
    """A broken package.json must not take the whole panel down."""
    assert plugin_with_manifest(tmp_path, "{not json")._version() == "unknown"


def test_missing_or_empty_version_field_says_unknown(tmp_path):
    for contents in ('{"name": "Shelfcloud"}', '{"version": ""}',
                     '{"version": "   "}', '{"version": null}',
                     '{"version": 3}'):
        (tmp_path / "package.json").write_text(contents, encoding="utf-8")
        set_plugin_dir(tmp_path)
        assert main.Plugin()._version() == "unknown", contents


def test_whitespace_is_trimmed(tmp_path):
    assert plugin_with_manifest(tmp_path, '{"version": "  2.0.1  "}')._version() == "2.0.1"


def test_the_value_is_cached(tmp_path):
    """A running plugin cannot have its files change under it."""
    plugin = plugin_with_manifest(tmp_path, json.dumps({"version": "1.0.0"}))
    assert plugin._version() == "1.0.0"
    (tmp_path / "package.json").write_text(json.dumps({"version": "9.9.9"}))
    assert plugin._version() == "1.0.0"
