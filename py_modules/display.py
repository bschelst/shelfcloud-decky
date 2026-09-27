# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Display size, and how much to scale a web UI on it.

Scale on the resolution a browser actually gets, not the hardware model. A Deck
docked to a 4K TV reports its dark 7" panel, the TV, and what gamescope hands
the browser all at once, and only that last one is worth scaling against.
Shared by the plugin and the launcher so there is one answer, not two.
"""

import glob
import os
import re
import subprocess
from typing import List, Tuple

# Judged on the shorter axis so an ultrawide reads by its height. Thresholds
# are that short edge, first match wins.
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

    xrandr first: under gamescope it reports the size apps are given, not the
    panel or the TV. DRM fallback reads the ENABLED connector, never merely
    connected (a docked Deck keeps its dark panel connected). Returns
    (0, 0, "unknown") instead of raising; no answer just means no scaling.
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

    `setting` is what the user chose: "auto", a number, or nothing. An unusable
    value falls back to auto rather than 1.0, and says so in `why`.
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
        # --window-size is device-independent px, so divide by scale to keep it
        # covering the display. Moot under --kiosk, matters for non-kiosk.
        out.append(f"--window-size={int(width / scale)},{int(height / scale)}")
    if scale != 1.0:
        # only force-device-scale-factor; the older --device-scale-factor in
        # guides is overridden by this one anyway.
        out.append(f"--force-device-scale-factor={scale}")
    return out
