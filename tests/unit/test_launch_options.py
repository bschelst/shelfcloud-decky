# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""A Steam shortcut's launch options must survive `/bin/sh -c`.

This pins down a bug that cost real debugging time and was invisible from the
Deck. Steam runs a shortcut as `sh -c "<exe> <options>"`. A GeForce NOW URL
ends `?game-id=<uuid>&lang=en_US`, and joining the arguments with plain spaces
leaves that `&` unquoted — a shell operator. `sh` then backgrounds everything
before it and treats the rest as a separate command, so the launcher gets a
URL truncated at the ampersand and never sees `--game-id` or `--provider`.

The game still starts from the truncated URL. Nothing looks broken. It just
silently stops asking the gateway, so no launch is resolved live and none is
ever counted — which is exactly how it went unnoticed.

Run: python3 -m pytest tests/unit
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
sys.modules["decky"] = decky

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
