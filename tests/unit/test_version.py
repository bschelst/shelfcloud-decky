# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The About box reports the version Decky provides, or admits it does not know.

_version() reads decky.DECKY_PLUGIN_VERSION rather than parsing package.json
again — Decky already exposes it as a env variable.
"""

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


def test_returns_the_version_decky_provides():
    main.decky.DECKY_PLUGIN_VERSION = "1.2.3"
    assert main.Plugin._version() == "1.2.3"


def test_empty_version_says_unknown():
    main.decky.DECKY_PLUGIN_VERSION = ""
    assert main.Plugin._version() == "unknown"


def test_none_version_says_unknown():
    main.decky.DECKY_PLUGIN_VERSION = None
    assert main.Plugin._version() == "unknown"
