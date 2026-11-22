# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""How big the display is, and how much to scale a web UI on it.

Must never key off the hardware model: a docked Deck reports "Jupiter" while
driving a 4K television, so scaling follows the actual resolution instead.
"""

import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import display  # noqa: E402


# ------------------------------------------------------------------- ladder

def test_the_handheld_gets_the_deck_recipe():
    assert display.auto_scale(1280, 800) == 1.25


def test_a_rotated_panel_reads_the_same():
    # judged by the short edge, so rotation does not matter
    assert display.auto_scale(800, 1280) == display.auto_scale(1280, 800)


def test_a_television_is_scaled_too():
    # 1080p is usualy a room away, so 1:1 is not readable enough
    assert display.auto_scale(1920, 1080) == 1.25


def test_bigger_displays_get_more():
    assert display.auto_scale(2560, 1440) == 1.5
    assert display.auto_scale(3840, 2160) == 2.0


def test_an_ultrawide_is_judged_by_its_height():
    # 3440x1440 is not 4K; on the long edge it would wrongly scale to 2.0
    assert display.auto_scale(3440, 1440) == 1.5


def test_an_unknown_display_is_left_alone():
    assert display.auto_scale(0, 0) == 1.0
    assert display.auto_scale(-1, 800) == 1.0


# ------------------------------------------------------------------ detect

def _xrandr(stdout):
    return mock.Mock(stdout=stdout)


def test_the_size_apps_are_given_wins_over_the_panel():
    """gamescope's size, not the television's and not the panel's."""
    out = ("Screen 0: minimum 16 x 16, current 1920 x 1080, maximum 32767 x 32767\n"
           "gamescope connected 1920x1080+0+0 950mm x 540mm\n")
    with mock.patch.object(display.subprocess, "run", return_value=_xrandr(out)):
        assert display.detect() == (1920, 1080, "xrandr")


def test_a_missing_xrandr_does_not_raise():
    with mock.patch.object(display.subprocess, "run", side_effect=FileNotFoundError), \
         mock.patch.object(display.glob, "glob", return_value=[]):
        assert display.detect() == (0, 0, "unknown")


def test_the_drm_fallback_skips_a_connected_but_disabled_panel(tmp_path):
    """A docked Deck keeps its dark panel connected; sizing off it would use a
    scherm nobody looks at."""
    panel = tmp_path / "card0-eDP-1"
    tv = tmp_path / "card0-DP-1"
    for folder, enabled, mode in ((panel, "disabled", "800x1280"),
                                  (tv, "enabled", "3840x2160")):
        folder.mkdir()
        (folder / "enabled").write_text(enabled + "\n")
        (folder / "modes").write_text(mode + "\n")

    with mock.patch.object(display.subprocess, "run", side_effect=OSError), \
         mock.patch.object(display.glob, "glob",
                           return_value=[str(panel) + "/", str(tv) + "/"]):
        width, height, how = display.detect()
    assert (width, height) == (3840, 2160)
    assert how == "drm:card0-DP-1"


# ------------------------------------------------------------------ resolve

def _at(width, height):
    return mock.patch.object(display, "detect", return_value=(width, height, "test"))


def test_auto_follows_the_display():
    with _at(1280, 800):
        assert display.resolve("auto")[3] == 1.25
    with _at(3840, 2160):
        assert display.resolve("auto")[3] == 2.0


def test_a_chosen_scale_beats_the_guess():
    with _at(1280, 800):
        width, height, how, scale, why = display.resolve("1.0")
    assert scale == 1.0
    assert why == "set by you"


def test_no_setting_at_all_means_auto():
    with _at(1280, 800):
        assert display.resolve(None)[3] == 1.25
        assert display.resolve("")[3] == 1.25


def test_a_setting_that_is_not_a_scale_falls_back_to_auto_and_says_so():
    # a typed value that cannot be read must be explained, not ignored
    with _at(1280, 800):
        _, _, _, scale, why = display.resolve("enormous")
    assert scale == 1.25
    assert "could not read" in why


def test_a_zero_scale_is_refused():
    with _at(1280, 800):
        _, _, _, scale, why = display.resolve("0")
    assert scale == 1.25
    assert "not a usable scale" in why


# -------------------------------------------------------------------- flags

def test_the_window_is_sized_in_scaled_pixels():
    # 1280x800 at 1.25 is 1024x640, computed rather than hardcoded
    with _at(1280, 800):
        flags = display.flags("auto")
    assert "--window-size=1024,640" in flags
    assert "--force-device-scale-factor=1.25" in flags


def test_only_one_scale_flag_is_passed():
    """--device-scale-factor is the old spelling and gets overridden, so we
    pass only --force-device-scale-factor."""
    with _at(1280, 800):
        flags = display.flags("auto")
    assert not any(f.startswith("--device-scale-factor") for f in flags)


def test_no_scale_flag_when_nothing_needs_scaling():
    with _at(1280, 800):
        flags = display.flags("1.0")
    assert not any("device-scale-factor" in f for f in flags)
    assert "--window-size=1280,800" in flags


def test_an_undetectable_display_gets_no_window_size():
    # for an unmeasured display, let the browser pick its own size
    with _at(0, 0):
        assert display.flags("auto") == []


def test_describe_says_what_was_found():
    with _at(1920, 1080):
        line = display.describe("auto")
    assert "1920x1080" in line
    assert "1.25" in line
    assert "auto" in line
