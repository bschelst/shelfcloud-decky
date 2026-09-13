#!/usr/bin/env python3
"""Launch one cloud game and press the provider's Play button.

This is what a Shelfcloud Steam shortcut actually runs. It exists because the
provider's streaming URL contains a session id minted server-side when Play is
pressed, so the game page cannot be linked to directly — something has to click.

Steam launches this, not the plugin, so the automation has to live here rather
than in the plugin backend.

Usage:
    shelfcloud-launch.py <browser_app_id> <profile_dir> <url> [--no-autoplay]

Exits when the browser exits, so Steam shows the game as running for the whole
session and the tile's playtime is accurate.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import cdp  # noqa: E402

CDP_PORT = 9223
PAGE_TIMEOUT = 45.0
CLICK_TIMEOUT = 60.0

# The provider's Play control. Matched on visible text because the markup is
# minified and class names change without warning; text is what the user sees
# and is far more stable.
CLICK_PLAY_JS = """
(() => {
  const wanted = ['play', 'spelen', 'jouer', 'spielen', 'jugar'];
  const candidates = [...document.querySelectorAll('button, a, [role=button]')];
  for (const el of candidates) {
    const text = (el.innerText || '').trim().toLowerCase();
    if (!text || text.length > 12) continue;
    if (!wanted.includes(text)) continue;
    const box = el.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;  // hidden
    el.click();
    return 'clicked:' + text;
  }
  return 'not-found';
})()
"""

IS_STREAMING_JS = "location.href.includes('streaming') || !!document.querySelector('video')"


def build_browser_command(app_id: str, profile: str, url: str) -> list:
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={CDP_PORT}",
        "--window-size=1280,800",
        "--class=shelfcloud",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--password-store=basic",
        "--disable-background-networking",
        "--autoplay-policy=no-user-gesture-required",
        "--kiosk",
        url,
    ]


def try_autoplay(url: str) -> None:
    """Press Play once the game page is up. Best effort — never fatal.

    If anything here fails the user simply presses Play themselves, which is the
    behaviour they would have had anyway. Nothing is worth killing the session
    over.
    """
    host_marker = url.split("/")[2] if "//" in url else url
    target = cdp.wait_for_page(CDP_PORT, host_marker, timeout=PAGE_TIMEOUT)
    if not target:
        print("[shelfcloud] game page never appeared; leaving Play to the user")
        return

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return

    deadline = time.monotonic() + CLICK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if cdp.evaluate(ws_url, IS_STREAMING_JS):
                print("[shelfcloud] already streaming")
                return
            result = cdp.evaluate(ws_url, CLICK_PLAY_JS)
            if isinstance(result, str) and result.startswith("clicked"):
                print(f"[shelfcloud] {result}")
                return
        except cdp.CDPError as exc:
            # Page may still be loading, or navigating between documents.
            print(f"[shelfcloud] waiting for Play ({exc})")
        time.sleep(1.5)

    print("[shelfcloud] Play button not found in time; leaving it to the user")


def active_gamepads() -> list:
    """Virtual gamepads Steam Input has created, as /dev/input/event* paths.

    Steam creates one per connected controller when a game starts. The browser
    enumerates these to expose the Gamepad API.
    """
    pads = []
    try:
        with open("/proc/bus/input/devices", encoding="utf-8") as handle:
            blocks = handle.read().split("\n\n")
    except OSError:
        return pads

    for block in blocks:
        if "X-Box" not in block and "Xbox" not in block:
            continue
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        pads.append(f"/dev/input/{handler}")
    return pads


def wait_for_gamepad(timeout: float = 6.0) -> list:
    """Give Steam Input a moment to create its virtual pads before starting the
    browser.

    The browser enumerates input devices at startup, and udev hotplug events do
    not reach it reliably inside the flatpak sandbox — so a pad that appears
    after it starts may never be noticed. Waiting costs a second and avoids
    launching into a game with no controller.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pads = active_gamepads()
        if pads:
            return pads
        time.sleep(0.3)
    return []


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--no-autoplay"]
    autoplay = "--no-autoplay" not in sys.argv[1:]

    if len(args) < 3:
        print(__doc__)
        return 2

    app_id, profile, url = args[0], args[1], args[2]
    os.makedirs(profile, exist_ok=True)

    pads = wait_for_gamepad()
    if len(pads) > 1:
        # Known issue: with several controllers connected the browser can bind
        # to the idle virtual pad and report no gamepad at all. Verified on
        # hardware — 4,473 events on the live pad, zero on the one Chrome
        # opened. Logged so it is diagnosable rather than mysterious.
        print(f"[shelfcloud] {len(pads)} virtual gamepads: {pads}. "
              "If the controller does not work in-game, disconnect the extra one.")
    elif not pads:
        print("[shelfcloud] no virtual gamepad yet; controller may not work")

    browser = subprocess.Popen(build_browser_command(app_id, profile, url))

    if autoplay:
        try:
            try_autoplay(url)
        except Exception as exc:  # never let automation break the launch
            print(f"[shelfcloud] autoplay failed, continuing: {exc}")

    return browser.wait()


if __name__ == "__main__":
    raise SystemExit(main())
