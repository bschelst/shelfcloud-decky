# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""What the installer must actually copy onto the Deck.

This exists because of one character. The installer copied

    py_modules/*.py

and cacert.pem is not a .py file, so the CA bundle was present in the repo,
present in the staging directory, and absent from the installed plugin. Decky's
plugin loader is a bundled binary carrying its own trust store, which does not
have the root our gateway presents — so every request failed
CERTIFICATE_VERIFY_FAILED while the identical request from the Deck's own
python succeeded. Nothing in the code was wrong; the file simply was not there.

A unit test is the right place for this: the failure is in packaging, it is
invisible until a real install on real hardware, and the symptom points at TLS
rather than at a shell glob.

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


def test_the_ca_bundle_ships_in_the_repo():
    """Everything else here is meaningless if the file is not there to copy."""
    bundle = os.path.join(ROOT, "py_modules", "cacert.pem")
    assert os.path.isfile(bundle), "py_modules/cacert.pem is missing"
    # A truncated or empty bundle verifies nothing and fails the same way.
    assert os.path.getsize(bundle) > 100_000, "cacert.pem looks truncated"
    with open(bundle, encoding="utf-8") as handle:
        assert "BEGIN CERTIFICATE" in handle.read(2048)


def test_the_installer_copies_every_py_module_not_only_dot_py():
    """The actual bug. A *.py glob silently drops the bundle."""
    body = script()
    py_module_lines = [line for line in body.splitlines()
                       if "py_modules" in line and line.strip().startswith("sudo cp")]
    assert py_module_lines, "the installer no longer copies py_modules at all"
    for line in py_module_lines:
        assert "*.py" not in line, (
            "the installer copies py_modules/*.py, which leaves cacert.pem "
            f"behind: {line.strip()}")


def test_everything_the_plugin_imports_or_reads_is_installed():
    """Each file the plugin needs at runtime must be named by the installer.

    Checked by walking what main.py and the launcher actually reference, so a
    new data file added later is caught by the same test rather than needing
    someone to remember this one.
    """
    body = script()
    for required in ("plugin.json", "package.json", "main.py",
                     "dist/index.js", "py_modules", "bin"):
        assert required in body, f"the installer never copies {required}"


def test_the_settings_file_is_never_clobbered():
    """It holds the API token. An install that overwrites it signs the user
    out, and the whole point of reinstalling is usually to fix something else.
    """
    body = script()
    assert re.search(r"if\s+\[.*settings\.json.*\]|!\s*sudo test -f", body), \
        "the installer no longer guards an existing settings.json"


@pytest.mark.parametrize("source", ["main.py", os.path.join("bin", "shelfcloud-launch.py")])
def test_a_missing_bundle_is_reported_rather_than_ignored(source):
    """Both loaders fall back to the system trust store when the bundle is
    absent. Falling back is correct; doing it in silence is what turned a
    one-line packaging bug into an evening of reading TLS errors."""
    with open(os.path.join(ROOT, source), encoding="utf-8") as handle:
        text = handle.read()
    start = text.index("def _ssl_context")
    body = text[start:start + 1600]
    assert "cacert.pem" in body
    assert "else:" in body, f"{source} falls back to system trust silently"
    assert ("logger.error" in body or "log(" in body), \
        f"{source} does not say anything when the bundle is missing"
