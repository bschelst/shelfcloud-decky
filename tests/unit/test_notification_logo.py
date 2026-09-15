# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Notifications carry the Shelfcloud mark, and carry it as a component.

Steam's notifications showed no icon at all: toaster.toast() takes an optional
`logo`, and none of the six call sites passed one.

The part worth protecting is HOW. `logo` is a ReactNode, not a path and not a
URL — and that is the only reason this works. This bundle runs inside Steam's
client at https://steamloopback.host, so an <img> pointing into the plugin
directory is a cross-origin request and does not load. It is the same rule that
forced game artwork through the Python backend and back as base64. There is no
image file to ship, and shipping one would be the mistake.

Sizing follows Steam's own notification layout, matched against the
steamstatus-decky plugin, which solved this first: a container fixed at 36px
with the mark at 1em inside it. The em is what matters — Steam scales
notification text with the system font size, so a hard pixel size is right on
one device and wrong on another.

Source-level tests: the assertions are about what the TSX says, because the
failure mode is a call site that quietly forgets the logo.

Run: python3 -m pytest tests/unit/test_notification_logo.py
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX = os.path.join(ROOT, "src", "index.tsx")
LOGO = os.path.join(ROOT, "src", "components", "Logo.tsx")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def code(path):
    """The source with comments removed.

    These files EXPLAIN the rules they follow — the Logo docstring says why an
    <img> cannot be used — so a naive substring search finds the prohibition
    and calls it a violation. Strip the prose and assert against the code.
    """
    source = read(path)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)       # block comments
    return "\n".join(re.sub(r"//.*$", "", line) for line in source.split("\n"))


# --------------------------------------------------------- every toast has one

def toast_blocks():
    """Each toaster.toast(...) call, as the source text around it."""
    source = read(INDEX)
    for match in re.finditer(r"toaster\.toast\(", source):
        chunk = source[match.start():match.start() + 500]
        end = chunk.find("});")
        yield source[:match.start()].count("\n") + 1, chunk[:end if end > 0 else 240]


def test_there_are_toasts_to_check():
    """Guards the guard: a regex that matches nothing would pass everything."""
    assert len(list(toast_blocks())) >= 5


def test_every_notification_carries_the_mark():
    """A toast without a logo is the bug this fixes — Steam draws a blank slot."""
    missing = [
        line for line, block in toast_blocks()
        # newGamesToast builds its own object; its returns are checked below.
        if "NotificationLogo" not in block and "newGamesToast" not in block
    ]
    assert not missing, f"toaster.toast without a logo at line(s): {missing}"


def test_the_new_games_notification_carries_it_on_every_branch():
    """It returns three different shapes — one per game count — and an early
    return is the easy one to miss."""
    source = read(INDEX)
    body = source[source.index("function newGamesToast"):]
    body = body[:body.index("\nexport default definePlugin")]
    assert body.count("NotificationLogo") == body.count("title:"), \
        "a newGamesToast branch returns without a logo"


# ------------------------------------------------- it must NOT be an image file

def test_the_mark_is_a_component_not_a_file():
    """An <img> into the plugin directory is cross-origin from
    steamloopback.host and silently fails to load."""
    logo = code(LOGO)
    assert "<svg" in logo
    assert "<img" not in logo
    for forbidden in ("DECKY_PLUGIN_DIR", "homebrew/plugins", ".png", ".jpg"):
        assert forbidden not in logo, f"Logo.tsx references {forbidden}"


def test_no_toast_points_at_a_file():
    source = read(INDEX)
    for line, block in toast_blocks():
        assert "<img" not in block, f"toast at line {line} uses an <img>"
        assert "url(" not in block, f"toast at line {line} points at a URL"


# ------------------------------------------------------------------- sizing

def test_the_notification_logo_is_sized_in_em_against_a_36px_base():
    """Matched against steamstatus-decky, which sizes its notification logo the
    same way: a 36px container with the icon at 1em.

    Pixels here would be wrong on a TV, where Steam scales notification text up
    and a fixed size stays small next to it.
    """
    logo = read(LOGO)
    block = logo[logo.index("export function NotificationLogo"):]
    assert 'fontSize: "36px"' in block
    assert 'width: "1em"' in block and 'height: "1em"' in block
    assert 'size="1em"' in block, "the mark itself must scale with the container"


def test_the_logo_component_accepts_an_em_size():
    """Logo took `size?: number`, which renders width={20} as pixels. The
    notification needs width="1em"."""
    logo = read(LOGO)
    assert "size?: number | string" in logo


def test_the_mark_is_cropped_so_a_given_size_looks_like_that_size():
    """The 256x256 source is ~39% empty space, so an uncropped mark at size=20
    draws about 12px of visible cloud and reads as undersized beside
    react-icons, which fill their box."""
    logo = read(LOGO)
    assert 'VIEW_BOX = "30 34 200 200"' in logo


def test_the_coloured_mark_is_used_not_the_monochrome_one():
    """LogoMono exists for Decky's plugin LIST, where neighbours recolour on
    selection. Nothing in a notification recolours, and the coloured mark is
    what makes a Shelfcloud toast recognisable among Steam's own."""
    logo = read(LOGO)
    block = logo[logo.index("export function NotificationLogo"):]
    assert "<Logo " in block
    assert "LogoMono" not in block


@pytest.mark.parametrize("path", [INDEX, LOGO])
def test_the_sources_exist(path):
    assert os.path.isfile(path), path
