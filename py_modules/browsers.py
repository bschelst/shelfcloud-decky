# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Browser detection and building the launch command.

Chromium-based browsers only (Edge, Chrome, Chromium and family). Cloud gaming
needs codec and DRM support that Firefox on Linux does not do well, so we do not
offer it: a browser that loads but then fails to stream is worse than none.
"""

import os
from typing import Dict, List, Optional

FLATPAK_BIN = "/usr/bin/flatpak"

FLATPAK_ROOTS = [
    "/var/lib/flatpak/app",
    os.path.expanduser("~/.local/share/flatpak/app"),
    "/home/deck/.local/share/flatpak/app",
]

# Chromium-based only, ordered by how well they do cloud gaming. Edge first
# because Xbox Cloud has always been the most picky about it.
KNOWN_BROWSERS = [
    {"app_id": "com.microsoft.Edge", "label": "Microsoft Edge"},
    {"app_id": "com.google.Chrome", "label": "Google Chrome"},
    {"app_id": "org.chromium.Chromium", "label": "Chromium"},
    {"app_id": "com.brave.Browser", "label": "Brave"},
    {"app_id": "com.vivaldi.Vivaldi", "label": "Vivaldi"},
    {
        "app_id": "io.github.ungoogled_software.ungoogled_chromium",
        "label": "Ungoogled Chromium",
    },
]

# Each flag hides something that would sit on top of the game in Game Mode.
BASE_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI",
    # Game Mode has no keyring, so without this the browser can hang on it.
    "--password-store=basic",
    "--disable-background-networking",
    # cloud streams must start without the user clicking the page.
    "--autoplay-policy=no-user-gesture-required",
    # Hardware video decode with VA-API. Without it the CPU decodes and we lose
    # frames and battery. SteamOS gives flatpak the GPU drivers, so this works.
    "--enable-features=VaapiVideoDecodeLinuxGL",
]


def detect_browsers() -> List[Dict]:
    """Return the supported browsers that are installed, in preference order."""
    found = []
    for browser in KNOWN_BROWSERS:
        for root in FLATPAK_ROOTS:
            if os.path.isdir(os.path.join(root, browser["app_id"])):
                found.append(dict(browser))
                break
    return found


# --------------------------------------------------------------- installation

# Browsers we offer to install with one press, best first. Only these two: each
# one gets downloaded onto someone's machine, so the list stays short and well
# known. The rest of KNOWN_BROWSERS still works, we just do not install it. Edge
# first for the same reason as above.
INSTALLABLE = [
    {
        "app_id": "com.microsoft.Edge",
        "label": "Microsoft Edge",
        "note": "Most reliable with Xbox Cloud.",
        "recommended": True,
    },
    {
        "app_id": "com.google.Chrome",
        "label": "Google Chrome",
        "note": "Works with all three services.",
        "recommended": False,
    },
]

FLATHUB_REPO = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# Long enough for a slow line to pull the browser plus its ~300 MB runtime,
# short enough that a stuck download fails instead of spinning for ever.
INSTALL_TIMEOUT = 15 * 60


def installable_browsers() -> List[Dict]:
    """The install offers, minus anything already present."""
    have = {b["app_id"] for b in detect_browsers()}
    return [dict(b) for b in INSTALLABLE if b["app_id"] not in have]


def is_installable(app_id: str) -> bool:
    """Is this one of the two we will install?

    Guards a value that comes from the frontend and ends up in an argv. Checked
    against a fixed list: there are only two right answers.
    """
    return any(b["app_id"] == app_id for b in INSTALLABLE)


def install_command(app_id: str) -> List[str]:
    """The exact argv to install one browser.

    A --user install, because plugin backends run as `deck`. It writes to
    ~/.local/share/flatpak, which detect_browsers scans and `flatpak run` finds,
    so the install shows up at once. No root, and no polkit prompt that Game
    Mode cannot show. argv, never a shell string.
    """
    return [
        FLATPAK_BIN, "install", "--user", "--assumeyes", "--noninteractive",
        "flathub", app_id,
    ]


def ensure_flathub_command() -> List[str]:
    """Add the Flathub remote for this user if it is missing.

    Does nothing on stock SteamOS, which has it. Kept because the install gives
    a bad error without it, and --if-not-exists makes it safe to always run.
    """
    return [
        FLATPAK_BIN, "remote-add", "--user", "--if-not-exists",
        "flathub", FLATHUB_REPO,
    ]


FLATPAK_OVERRIDES = [
    "--filesystem=/run/udev:ro",
    "--device=all",
]


def override_command(app_id: str) -> List[str]:
    """Give a browser the sandbox permissions cloud gaming needs.

    --filesystem=/run/udev:ro lets it list devices via udev. --device=all gives
    read access to /dev/input/*, where the gamepad data is. Without it
    getGamepads() returns four empty slots and the cloud offers no controller.
    A --user override, so it survives browser updates and needs no root.
    """
    return [FLATPAK_BIN, "--user", "override", *FLATPAK_OVERRIDES, app_id]


def profile_dir(app_id: str) -> str:
    """A browser profile that stays, one per browser.

    Without it the browser starts logged out every launch, so the user would
    have to sign in on every Play. The most important part of the command.
    """
    base = os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", "/tmp/shelfcloud")
    return os.path.join(base, "browser-profiles", app_id)




def resolve_browser(preferred: Optional[str]) -> Optional[Dict]:
    """The browser to use: the user's choice if installed, else the best one.

    Returns None when nothing supported is installed; the UI then asks to
    install one instead of failing the launch.
    """
    installed = detect_browsers()
    if not installed:
        return None
    if preferred:
        for browser in installed:
            if browser["app_id"] == preferred:
                return browser
    return installed[0]
