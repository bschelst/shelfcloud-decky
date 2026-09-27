# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""A Steam shortcut's launch options must survive `/bin/sh -c`.

Steam runs a shortcut as `sh -c "<exe> <options>"`. An unquoted `&` in a URL
(GeForce NOW ends `&lang=en_US`) is a shell operator, so a plain-space join
truncates the URL there and the launcher never sees --game-id or --provider.
The game still starts, so the broken gateway call went unnoticed.
"""

import json
import os
import shlex
import subprocess
import sys
import types
from unittest import mock

# Decky's runtime is not importable off-device; stub what main.py touches.
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

from main import _shell_join  # noqa: E402

GFN_URL = ("https://play.geforcenow.com/games"
           "?game-id=46bfab06-d864-465d-9e56-2d9e45cdee0a&lang=en_US")

PARTS = ["/plugins/Shelfcloud/bin/shelfcloud-launch.py", "com.microsoft.Edge",
         "/data/browser-profiles/com.microsoft.Edge", GFN_URL,
         "--game-id", "1", "--provider", "geforce_now"]


def through_shell(options: str):
    """Run the options the way Steam does and report the argv that arrives."""
    harness = "import sys,json;print(json.dumps(sys.argv[1:]))"
    result = subprocess.run(
        ["sh", "-c", f"{sys.executable} -c {shlex.quote(harness)} {options}"],
        capture_output=True, text=True,
        # bounded with stdin closed, else a child waiting for input hangs the suite
        timeout=30, stdin=subprocess.DEVNULL,
    )
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []


def test_the_launcher_receives_every_argument():
    argv = through_shell(_shell_join(PARTS))
    assert argv == PARTS, argv


def test_the_url_survives_its_ampersand():
    """Truncation loses &lang=en_US and would silently change the page."""
    assert GFN_URL in through_shell(_shell_join(PARTS))


def test_game_id_and_provider_reach_the_launcher():
    """Without these the gateway is never asked and the launch is not counted."""
    argv = through_shell(_shell_join(PARTS))
    assert argv[argv.index("--game-id") + 1] == "1"
    assert argv[argv.index("--provider") + 1] == "geforce_now"


def test_a_plain_join_really_does_break_it():
    """The counter-test: without this, the fix above proves nothing."""
    argv = through_shell(" ".join(PARTS))
    assert "--game-id" not in argv
    assert GFN_URL not in argv


def test_paths_containing_spaces_survive():
    parts = ["/plugins/Shelfcloud/bin/shelfcloud-launch.py", "com.google.Chrome",
             "/data/browser profiles/Chrome", "https://example.test/a?b=1&c=2",
             "--game-id", "42"]
    assert through_shell(_shell_join(parts)) == parts


def test_other_shell_metacharacters_are_neutralised():
    """A game name or URL is provider-supplied; it must never become a command."""
    for hostile in ("https://x/?a=1;touch /tmp/pwned", "https://x/?a=$(id)",
                    "https://x/?a=`id`", "https://x/?a=1|cat", "https://x/?a=1>out"):
        argv = through_shell(_shell_join(["/l.py", "b", "/p", hostile, "--game-id", "1"]))
        assert argv[3] == hostile, hostile
        assert argv[-1] == "1"


def test_quoting_is_a_no_op_for_ordinary_arguments():
    """It should not make simple options unreadable in the Steam UI."""
    assert _shell_join(["/l.py", "com.google.Chrome", "--no-autoplay"]) == \
        "/l.py com.google.Chrome --no-autoplay"
