"""Browser detection and launch-command construction.

Shelfcloud is not tied to one browser, but it is limited to Chromium-based ones:
Edge, Chrome, Chromium and their relatives. Cloud gaming depends on codec and
DRM support that Firefox on Linux does not handle reliably, so Firefox is not
offered at all — a browser that loads the page and then fails to stream is worse
than being told to install a supported one.
"""

import os
from typing import Dict, List, Optional

FLATPAK_BIN = "/usr/bin/flatpak"

FLATPAK_ROOTS = [
    "/var/lib/flatpak/app",
    os.path.expanduser("~/.local/share/flatpak/app"),
    "/home/deck/.local/share/flatpak/app",
]

# Chromium-based browsers only, ordered by how well they handle cloud gaming.
# Edge leads because Xbox Cloud has historically been fussiest about it.
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

# Each flag suppresses something that would otherwise sit on top of the game in
# Game Mode, where there is no way to dismiss it.
BASE_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI",
    # Game Mode has no keyring daemon; without this the browser can stall
    # waiting on a secret store that will never answer.
    "--password-store=basic",
    "--disable-background-networking",
    # Cloud streams must be able to start without the user clicking the page.
    "--autoplay-policy=no-user-gesture-required",
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


def profile_dir(app_id: str) -> str:
    """A persistent browser profile, kept per browser.

    Without this the browser starts logged out on every launch, so the user
    would have to sign in to the cloud provider every single time they press
    Play. This is the most important part of the launch command.
    """
    base = os.environ.get("DECKY_PLUGIN_RUNTIME_DIR", "/tmp/shelfcloud")
    return os.path.join(base, "browser-profiles", app_id)


def build_launch(app_id: str, url: str, width: int = 1280, height: int = 800) -> Dict[str, str]:
    """Build the Steam shortcut exe + launch options that open one game.

    Returns {"exe": ..., "options": ...} ready for SteamClient.Apps.AddShortcut.
    """
    profile = profile_dir(app_id)
    options = [
        "run",
        "--branch=stable",
        "--arch=x86_64",
        # The profile lives outside the sandbox's default home, so flatpak has
        # to be granted access explicitly or the profile silently will not
        # persist — defeating the point of --user-data-dir.
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        # Lets window matching target this window rather than another browser's.
        "--class=shelfcloud",
        *BASE_FLAGS,
        "--kiosk",
        # Positional, deliberately: --app= also hides the toolbar but behaves
        # differently for the popups and fullscreen requests streams rely on.
        url,
    ]
    return {"exe": FLATPAK_BIN, "options": " ".join(options)}


def resolve_browser(preferred: Optional[str]) -> Optional[Dict]:
    """The browser to use: the user's choice if installed, else the best available.

    Returns None when no supported browser is installed, which the UI turns into
    a prompt to install one rather than a failed launch.
    """
    installed = detect_browsers()
    if not installed:
        return None
    if preferred:
        for browser in installed:
            if browser["app_id"] == preferred:
                return browser
    return installed[0]
