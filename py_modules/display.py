# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""How big the display is, and how much to scale a web UI on it.

A cloud service's web interface is built for a desktop monitor at arm's length.
On a 7" handheld it is small; across a room on a television it is smaller
still. Chromium can scale it with --force-device-scale-factor, and the recipe
that circulates for the Steam Deck is 1.25.

What it must NOT be keyed on is the hardware model. Measured on a Steam Deck
docked to a 4K television, all three of these were different numbers at once:

    card0-eDP-1   connected, enabled=disabled   the 7" panel, switched off
    card0-DP-1    connected, 3840x2160          the television
    gamescope     1920x1080                     what the browser actually gets

The third is the one a browser sees, and the only one worth scaling against.
`/sys/class/dmi/id/product_name` says "Jupiter" in every one of those cases, so
a "Steam Deck mode" switch would have applied the handheld recipe to a
television — the exact thing this is meant to avoid.

Shared by the plugin and the launcher: the plugin shows the user what was
detected, the launcher acts on it, and a second copy of this would be two
answers to one question.
"""

import glob
import os
import re
import subprocess
from typing import List, Tuple

# Judged on the SHORTER axis, so an ultrawide is read by its height rather than
# mistaken for an enormous display and scaled into uselessness. Thresholds are
# that short edge; the first match wins.
SCALE_LADDER: List[Tuple[int, float]] = [
    (900, 1.25),      # 1280x800 handheld, 1366x768 — the Deck's own recipe
    (1150, 1.25),     # 1920x1080: a television, usually across a room
    (1500, 1.5),      # 1440p
    (10 ** 6, 2.0),   # 4K and beyond
]

# What the settings dropdown offers beside "auto".
SCALE_CHOICES: List[float] = [1.0, 1.25, 1.5, 2.0]

DETECT_TIMEOUT = 5


def detect() -> Tuple[int, int, str]:
    """(width, height, how) of the display a browser will open on.

    xrandr first, because under gamescope it reports the size apps are given
    rather than the panel's or the television's — see the note above.

    The DRM fallback is for a session with no xrandr. It reads the connector
    that is actually ENABLED, never merely connected: a docked Deck keeps its
    dark internal panel connected the whole time, and reading that would size
    everything for a screen nobody is looking at.

    Returns (0, 0, "unknown") rather than raising. Nothing here is worth
    failing a launch over — no answer simply means no scaling.
    """
    try:
        out = subprocess.run(
            ["xrandr", "--current"],
            capture_output=True, text=True, timeout=DETECT_TIMEOUT,
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")},
        ).stdout
        found = re.search(r"current\s+(\d+)\s*x\s*(\d+)", out)
        if found:
            return int(found.group(1)), int(found.group(2)), "xrandr"
    except Exception:
        pass

    try:
        for path in sorted(glob.glob("/sys/class/drm/card*/card*-*/")):
            try:
                with open(os.path.join(path, "enabled"), encoding="utf-8") as handle:
                    if handle.read().strip() != "enabled":
                        continue
                with open(os.path.join(path, "modes"), encoding="utf-8") as handle:
                    mode = handle.readline().strip()
            except Exception:
                continue
            found = re.match(r"(\d+)x(\d+)", mode)
            if found:
                name = os.path.basename(path.rstrip("/"))
                return int(found.group(1)), int(found.group(2)), f"drm:{name}"
    except Exception:
        pass

    return 0, 0, "unknown"


def auto_scale(width: int, height: int) -> float:
    """The scale a display of this size wants, or 1.0 if it is not known."""
    if width <= 0 or height <= 0:
        return 1.0
    short_edge = min(width, height)
    for limit, scale in SCALE_LADDER:
        if short_edge < limit:
            return scale
    return 1.0


def resolve(setting) -> Tuple[int, int, str, float, str]:
    """Everything a caller needs: (width, height, how, scale, why).

    `setting` is whatever the user chose — "auto", a number, or nothing at all.
    An unusable value falls back to auto rather than to 1.0, and says so in
    `why`, because silently ignoring a setting someone typed is how a wrong
    scale goes unexplained.
    """
    width, height, how = detect()

    wanted = setting
    if isinstance(wanted, str):
        wanted = wanted.strip().lower()
    if not wanted or wanted == "auto":
        return width, height, how, auto_scale(width, height), "auto"

    try:
        scale = float(wanted)
    except (TypeError, ValueError):
        return (width, height, how, auto_scale(width, height),
                f"auto (could not read setting {setting!r})")
    if scale <= 0:
        return (width, height, how, auto_scale(width, height),
                "auto (setting was not a usable scale)")
    return width, height, how, scale, "set by you"


def describe(setting) -> str:
    """One line for a log or a settings panel."""
    width, height, how, scale, why = resolve(setting)
    size = f"{width}x{height}" if width and height else "unknown"
    return f"{size} via {how}; interface scale {scale} ({why})"


def flags(setting) -> List[str]:
    """Chromium flags that size and scale the window for this display."""
    width, height, _, scale, _ = resolve(setting)
    out: List[str] = []
    if width and height:
        # --window-size takes device-independent pixels, so dividing by the
        # scale keeps the window covering the display once scaling applies.
        # Largely moot under --kiosk, which goes fullscreen regardless; it is
        # what sizes the window for the launches that are not kiosk.
        out.append(f"--window-size={int(width / scale)},{int(height / scale)}")
    if scale != 1.0:
        # Only --force-device-scale-factor. The --device-scale-factor seen
        # beside it in guides is an older spelling of the same idea and is
        # overridden by this one, so passing both says nothing twice.
        out.append(f"--force-device-scale-factor={scale}")
    return out
