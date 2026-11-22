# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""What the installer must actually copy onto the Deck.

Run: python3 -m pytest tests/unit
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INSTALLER = os.path.join(ROOT, "packaging", "install-to-deck.sh")


def script():
    with open(INSTALLER, encoding="utf-8") as handle:
        return handle.read()


def test_everything_the_plugin_imports_or_reads_is_installed():
    body = script()
    for required in ("plugin.json", "package.json", "main.py",
                     "dist/index.js", "py_modules", "bin"):
        assert required in body, f"the installer never copies {required}"


def test_the_settings_file_is_never_clobbered():
    body = script()
    assert re.search(r"if\s+\[.*settings\.json.*\]|!\s*sudo test -f", body), \
        "the installer no longer guards an existing settings.json"


def test_the_release_workflow_packages_all_runtime_files():
    workflow = os.path.join(ROOT, ".github", "workflows", "release.yml")
    with open(workflow, encoding="utf-8") as handle:
        body = handle.read()
    for required in ("plugin.json", "package.json", "main.py", "LICENSE",
                     "dist/index.js", "py_modules", "bin"):
        assert required in body, f"release workflow does not package {required}"
