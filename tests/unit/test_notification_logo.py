# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Notifications carry the Shelfcloud mark, and carry it as a component.

`logo` is a ReactNode, not a path or URL. An <img> into the plugin dir is
cross-origin from steamloopback.host and would not load, so the mark ships as
a component and is sized in em against a 36px base (like steamstatus-decky).
Tests read the TSX, since the failure mode is a call site that forgets the logo.
"""

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INDEX = os.path.join(ROOT, "src", "index.tsx")
LOGO = os.path.join(ROOT, "src", "components", "Logo.tsx")
COMPONENTS_DIR = os.path.join(ROOT, "src", "components")


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def code(path):
    """The source with comments stripped, so prose about the rules is not
    mistaken for a violation by a substring search."""
    source = read(path)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)       # block comments
    return "\n".join(re.sub(r"//.*$", "", line) for line in source.split("\n"))


# --------------------------------------------------------- every toast has one

def _tsx_files():
    """All .tsx files that may contain toast calls."""
    yield INDEX
    for name in sorted(os.listdir(COMPONENTS_DIR)):
        if name.endswith(".tsx"):
            yield os.path.join(COMPONENTS_DIR, name)


def toast_blocks():
    """Each toaster.toast(...) call across index.tsx and the components."""
    for path in _tsx_files():
        source = code(path)
        for match in re.finditer(r"toaster\.toast\(", source):
            # enough context to include the logo prop
            chunk = source[match.start():match.start() + 800]
            end = chunk.find("});")
            yield path, source[:match.start()].count("\n") + 1, chunk[:end + 3 if end > 0 else 240]


def test_there_are_toasts_to_check():
    """Guards the guard: a regex that matches nothing would pass everyting."""
    assert len(list(toast_blocks())) >= 5


def test_every_notification_carries_the_mark():
    """A toast without a logo is the bug this fixes — Steam draws a blank slot."""
    missing = [
        f"{path}:{line}" for path, line, block in toast_blocks()
        if "NotificationLogo" not in block and "newGamesToast" not in block
    ]
    assert not missing, f"toaster.toast without a logo at: {missing}"


def test_the_new_games_notification_carries_it_on_every_branch():
    """Three return shapes, one per game count, and a early return is the
    easy branch to miss."""
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
    for path, line, block in toast_blocks():
        assert "<img" not in block, f"toast at {path}:{line} uses an <img>"
        assert "url(" not in block, f"toast at {path}:{line} points at a URL"


# ------------------------------------------------------------------- sizing

def test_the_notification_logo_is_sized_in_em_against_a_36px_base():
    """36px container with the icon at 1em. Pixels would be wrong on a TV,
    where Steam scales notification text up."""
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
    """The 256x256 source is ~39% empty space, so uncropped it reads as
    undersized beside react-icons, which fill their box."""
    logo = read(LOGO)
    assert 'VIEW_BOX = "30 34 200 200"' in logo


def test_the_coloured_mark_is_used_not_the_monochrome_one():
    """LogoMono is for the plugin list, where neighbours recolour on select.
    A notification never recolours, and the coloured mark is what makes a
    Shelfcloud toast stand out among Steam's own."""
    logo = read(LOGO)
    block = logo[logo.index("export function NotificationLogo"):]
    assert "<Logo " in block
    assert "LogoMono" not in block


@pytest.mark.parametrize("path", [INDEX, LOGO])
def test_the_sources_exist(path):
    assert os.path.isfile(path), path
