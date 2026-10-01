#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Launch one cloud game and press the provider's Play button.

What a Shelfcloud Steam shortcut runs. The streaming URL carries a session id
made when Play is pressed, so the game page cannot be linked to and something
has to click. Steam runs this, not the plugin.

Usage:
    shelfcloud-launch.py <browser_app_id> <profile_dir> [url]
                         [--game-id N] [--provider KEY] [--no-autoplay] [--no-kiosk]

A game tile passes no url; --game-id lets the gateway say where the game goes
now, so a changed URL or provider works without re-syncing, and synced.json is
the offline fallback. [url] is still taken for the website tile and old
shortcuts. Exits when the browser exits so Steam counts playtime right.
"""

import glob
import ipaddress
import json
import os
import signal
import ssl
import subprocess
import threading
import sys
import urllib.parse
import urllib.request
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import browsers  # noqa: E402
import display  # noqa: E402
import weburl  # noqa: E402
import cdp  # noqa: E402
from autoplay import (  # noqa: E402
    CLICK_PLAY_JS, PLAY_WORDS, DENY_WORDS, DENY_PHRASES,
    IS_STREAMING_JS, LIST_CANDIDATES_JS, PROVIDERS, STREAM_STATS_JS,
    provider_profile,
)
from luna_shims import (  # noqa: E402
    NEUTRALISE_APP_HANDOFF_JS, VIRTUAL_GAMEPAD_JS, FULLSCREEN_CHECK_JS,
)

CDP_PORT = 9223

# Must be something: the proxy in front of the gateway rejects urllib's default with 403.
USER_AGENT = "Shelfcloud-Launcher/0.1 (+https://github.com/bschelst/shelfcloud)"
PAGE_TIMEOUT = 45.0
CLICK_TIMEOUT = 60.0

# Wait for a first frame before giving up on measuring. Generous, a queue on a
# busy evening is minutes. Runs next to the session so it costs nothing.
STREAM_STATS_TIMEOUT = 180.0

# The gateway. Fixed and https, NOT read from settings.json: that key holds
# whatever plaintext LAN address the plugin was last aimed at, which put the
# bearer token on the wire in clear. Must match DEFAULT_GATEWAY in main.py,
# tests/unit/test_gateway_default.py checks they agree.
GATEWAY = "https://shelfcloud.cc"

# Where the gateway used to live, still allowed during the changeover, see below
LEGACY_HOST = "shelfcloud.schelstraete.org"

# The only places a game may launch. Checked HERE on the device, not just on
# the gateway: a server can be compromised and its URL opens in a kiosk browser
# holding live provider sessions, with no address bar to show where it went.
#
# Luna is the FULL host, not "amazon.com": these are suffix rules, so
# "amazon.com" would let in every Amazon host (retail, S3 subdomains). Only
# luna.amazon.com is needed, which is also why the catalog stores it instead of
# the country-specific play.amazon.<tld> that Luna returns.
ALLOWED_LAUNCH_DOMAINS = (
    "geforcenow.com",     # play.geforcenow.com
    "boosteroid.com",     # cloud.boosteroid.com
    "xbox.com",           # www.xbox.com
    "luna.amazon.com",    # full host on purpose, see above
    # Our own site, for the "Shelfcloud (website)" tile — Steam's own browser
    # sends no controller input, so the catalogue needs one under Steam Input.
    # Taken from GATEWAY so the two cannot drift; this is the host the launcher
    # already asks where every game goes.
    urllib.parse.urlsplit(GATEWAY).hostname or "",
    # The old home, kept while devices catch up. The gateway resolves a tile's
    # address at launch, so allowing both means the tile works whichever side of
    # the update a device is on. Drop it once every device is updated.
    LEGACY_HOST,
)


def _ssl_context():
    """Verified TLS, using certifi's CA bundle if available.

    Runs standalone (not in Decky's sandbox) so it cannot import Decky's
    helpers, but certifi is on the system path and reachable directly.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def is_allowed_launch_url(url) -> bool:
    """Is this somewhere a cloud game may really be played?

    https only: a launch carries provider session cookies. The host must BE an
    allowed domain or sit under it; the dot in `"." + domain` is the whole
    check, without it "xbox.com.evil.com" would pass. Credentials in the
    authority are refused, "https://play.geforcenow.com@evil.com/" reads as
    though evil.com is not the host.

    A backslash, tab or newline is refused before parsing: urlsplit follows
    RFC 3986 (where "\\" is normal) but Chromium follows WHATWG (where "\\" ends
    the authority like "/"), so "https://evil.com\\cloud.boosteroid.com/" is
    host evil.com to the browser but passes the suffix check here. No real
    provider URL has any of them.
    """
    if not url or not isinstance(url, str):
        return False
    # On the RAW string: .strip() would drop a trailing newline thats part of the attack.
    if any(c in url for c in "\\\t\r\n"):
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_LAUNCH_DOMAINS)

# Gateway address and credential. Steam starts the launcher, not the plugin,
# so it reads these itself.
SETTINGS_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/settings.json")

# What the plugin remembers per tile, keyed by game id. The offline launch URL
# lives here, not in the shortcut, because Steam shows launch options verbatim.
STATE_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/synced.json")
RESOLVE_TIMEOUT = 8


def stored_fallback(game_id: str, provider: str = "") -> str:
    """The offline launch URL for this game, or "" if there is not a usable one.

    Never raises: runs after the gateway has already failed, so a bad state file
    must degrade to "no fallback". The URL is checked again here since the file
    is read exactly when the gateway cannot be asked to correct it.
    """
    if not game_id:
        return ""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        # Keyed "<game id>:<provider>" now, a game can have a tile per service.
        # The bare id is still tried for entries written before that change.
        entry = state.get(f"{game_id}:{provider}") if provider else None
        if entry is None:
            entry = state.get(str(game_id))
        if entry is None:
            entry = next((v for k, v in state.items()
                          if k.split(":", 1)[0] == str(game_id)), None)
        url = (entry or {}).get("url")
    except Exception:
        return ""
    return url if isinstance(url, str) and url_is_acceptable(url, provider) else ""

# Steam catches none of this script's output, so we keep our own log. A silent
# fallback to the stored URL is otherwise invisible: the game starts either way,
# only the log says wether the gateway was ever reached.
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOMEBREW_DIR = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
LOG_FILE = os.path.join(_HOMEBREW_DIR, "logs", "Shelfcloud", "shelfcloud-launch.log")

# Kept small: lives on Deck internal storage and nothing rotates it, so it trims itself.
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_BYTES = 128 * 1024


def log(message: str) -> None:
    """Write one timestamped line, to the log file and to stdout.

    Never raises: dying because it could not log would be worse than the bug
    this exists to find.
    """
    line = f"[shelfcloud] {message}"
    print(line)  # keep: log() is the writer
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            with open(LOG_FILE, "rb") as handle:
                handle.seek(-LOG_KEEP_BYTES, os.SEEK_END)
                handle.readline()          # drop the half line we landed in
                tail = handle.read()
            with open(LOG_FILE, "wb") as handle:
                handle.write(tail)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except Exception:
        pass


def configured_scale():
    """What the user chose for the interface scale, or "auto".

    Read at launch, not baked into the shortcut: else changing one number would
    mean re-syncing hundreds of shortcuts opnieuw.
    """
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            return json.load(handle).get("ui_scale", "auto")
    except Exception:
        return "auto"


def display_flags() -> list:
    """Window size and scale for this display, with a line in the log.

    Logged because it is set before the browser paints, so a wrong guess only
    shows up as a wrong-size UI with nothing to say why.
    """
    setting = configured_scale()
    log("display: " + display.describe(setting))
    return display.flags(setting)


def resolve_launch(game_id: str, fallback_url: str, provider: str = "") -> tuple:
    """Ask the gateway where this game launches right now.

    The URL used to be frozen into the shortcut at sync time, so a changed URL
    or provider stayed broken until the next sync. Asking at launch fixes that
    and lets the gateway count what gets played. The frozen URL is kept as a
    fallback so a sleeping gateway on the LAN never makes a game unplayable,
    which is also why the timeout is short. Every path out is logged with its
    reason, since a fallback looks just like success from the outside.
    """
    # Look up the offline URL first so every path below has a fallback. An old
    # shortcut may still pass one positionally; that wins only when nothing is stored.
    fallback_url = fallback_url or stored_fallback(game_id, provider)

    if not game_id:
        log("no --game-id on this shortcut; using the stored link "
            "(re-sync to get gateway resolution)")
        return fallback_url, "baked-in"

    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            settings = json.load(handle)
    except Exception as exc:
        log(f"cannot read {SETTINGS_FILE} ({exc}); using the stored link")
        return fallback_url, "baked-in"

    # The constant above, NOT settings["gateway_url"]: older builds still write
    # that key with a plaintext LAN address, which leaks the token on the wire.
    gateway = GATEWAY
    token = settings.get("api_token")
    if not token:
        log("no credential in settings — not signed in; using the stored link. "
            "This launch will not be counted.")
        return fallback_url, "not signed in"

    try:
        # The provider goes WITH the request. Without it the gateway picks by its
        # own ranking, wrong when a game has one tile per service: three Fortnite
        # tiles share a game_id and would all open the same service.
        endpoint = f"{gateway}/api/me/launch/{game_id}"
        if provider:
            endpoint += "?provider=" + urllib.parse.quote(provider, safe="")
        request = urllib.request.Request(
            endpoint,
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # Without this, urllib sends "Python-urllib/3.x" and the proxy
                # answers 403 before the app sees it. Any other User-Agent gets
                # through and a bad token gets a normal 401.
                "User-Agent": USER_AGENT,
            },
        )
        started = time.monotonic()
        # Verified TLS against the bundled CA bundle: Decky's Python ships its own
        # libssl and has been seen missing roots a Cloudflare host presents.
        with urllib.request.urlopen(request, timeout=RESOLVE_TIMEOUT,
                                    context=_ssl_context()) as response:
            payload = json.loads(response.read().decode())
        elapsed = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        # Tell "gateway said no" apart from "gateway not there": a 401 needs a
        # re-login, a refused connection needs the box woken up.
        status = getattr(exc, "code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        log(f"gateway {gateway} did not answer for game {game_id} "
            f"({detail}: {exc}); using the stored link")
        return fallback_url, "baked-in"

    url = payload.get("launch_url") if isinstance(payload, dict) else None
    if not url:
        log(f"gateway answered for game {game_id} with no launch_url "
            f"({str(payload)[:120]}); using the stored link")
        return fallback_url, "baked-in"

    # Do not trust the gateway with where this device browses. A good gateway
    # only returns a permitted host; anything else is compromised and stops here.
    if not url_is_acceptable(url, provider):
        log(f"gateway returned a URL this tile may not open "
            f"({str(url)[:120]!r}) — REFUSED, using the stored link")
        return fallback_url, "refused gateway url"

    log(f"gateway resolved game {game_id} -> {payload.get('name') or '?'} "
        f"via {payload.get('provider') or '?'} in {elapsed} ms")
    return url, f"gateway ({payload.get('provider')})"

# Where a page tile may go, and which providers use that rule instead of the
# provider allowlist above. In weburl.py, which main.py imports too, so making
# a shortcut and opening it share one copy of the rule.
WEBSITE_PROVIDERS = weburl.WEBSITE_PROVIDERS
is_allowed_website_url = weburl.is_allowed_website_url


def build_website_command(app_id: str, profile: str, url: str) -> list:
    """Chromium command line for a page, not the one a game gets.

    Three differences, each a reason the allowlist can be looser here:
    * A separate profile, so a page cannot reach the live provider sessions.
    * No remote debugging port: it is unauthenticated and only worth it for the
      fixed set of provider sites, not an arbitrary page.
    * No kiosk, so the user keeps the address bar to see where they are.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        *display_flags(),
        "--start-maximized",
        "--class=shelfcloud-web",
        *browsers.BASE_FLAGS,
        url,
    ]


def website_profile(profile: str) -> str:
    """Where a page's cookies live: beside the game profile, never inside it."""
    return os.path.join(os.path.dirname(profile.rstrip("/")) or ".",
                        os.path.basename(profile.rstrip("/")) + "-web")

def url_is_acceptable(url, provider: str) -> bool:
    """Which of the two gates applies, by the kind of tile this is.

    One function so the three places a URL is checked cannot disagree about
    which rule a page is held to.
    """
    if provider in WEBSITE_PROVIDERS:
        return is_allowed_website_url(url)
    return is_allowed_launch_url(url)


def build_browser_command(app_id: str, profile: str, url: str, kiosk: bool = True,
                          user_agent: str = "") -> list:
    """Chromium command line for one session.

    Kiosk is right for streaming and wrong for signing in: a login can bounce
    through pages that need a visible tab strip and a way back.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={CDP_PORT}",
        *display_flags(),
        "--class=shelfcloud",
        *browsers.BASE_FLAGS,
        *([f"--user-agent={user_agent}"] if user_agent else []),
        *(["--kiosk"] if kiosk else []),
        url,
    ]


# Luna wants the desktop app to take over, and a Deck has none. It pokes a
# luna:// scheme at a hidden #hiddenIframe and reads 500ms of silence as "app
# not installed". Chromium turns that poke into an "open xdg-open?" prompt that
# sits on top of the game, and no browser flag can switch it off.
#
# So we make #hiddenIframe ourselves, with a location.href setter that does
# nothing. Luna finds ours, pokes the stub, hears nothing and takes its own
# "not installed" branch, which streams in the browser. An own property shadows
# the prototype getter because Location.href is [Unforgeable].


def wake_gamepads(url: str, provider: str = "") -> None:
    """No-op. The virtual gamepad override broke real gamepad detection on
    Boosteroid and GeForce NOW by hiding the native getGamepads() result.
    Luna's injection lives in hold_platform_override and is not affected."""
    pass



def _safely(work, *args) -> None:
    """Run a diagnostic on a thread without letting it take the launch down.

    A thread error does not kill the process, but it dumps a traceback into a
    log people read when something is already wrong. One line is more use.
    """
    try:
        work(*args)
    except Exception as exc:
        log(f"{getattr(work, '__name__', 'diagnostic')} failed, continuing: {exc}")


def log_stream_resolution(url: str, provider: str = "") -> None:
    """Record what the stream actually came down at, once one is running.

    This is the open question behind the scaling. A higher scale shrinks the CSS
    viewport, and whether that costs picture quality depends on if the provider
    sizes its stream from CSS pixels or real ones, which differs per provider and
    is nowhere documented, so we measure it. Read `stream` against `css` and
    `physical` in the log:
      stream == physical  the interface got bigger for nothing
      stream == css       the scaling costs resolution, check the ladder in display.py

    Best effort and never fatal: a diagnostic, not a feature.
    """
    profile = provider_profile(provider)
    marker = profile.get("page_match") or (url.split("/")[2] if "//" in url else url)
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target or not target.get("webSocketDebuggerUrl"):
        return
    ws_url = target["webSocketDebuggerUrl"]

    # A stream does not start the moment Play is pressed: there is a queue and a
    # first frame. Polling beats one timed guess.
    deadline = time.monotonic() + STREAM_STATS_TIMEOUT
    while time.monotonic() < deadline:
        try:
            stats = cdp.evaluate(ws_url, STREAM_STATS_JS)
        except cdp.CDPError:
            return                      # page gone; the session is over
        if stats:
            log(f"stream: {stats}")
            return
        time.sleep(2)


def log_browser_gamepads(url: str, provider: str = "") -> None:
    """Record what the PAGE sees, the only view that decides anything.

    The web player reads navigator.getGamepads() and drives whatever sits at
    index 0. If index 0 is the Deck's own controls, an external controller does
    nothing until Steam's order is changed, and Steam does not keep that order
    for these tiles. Chrome only shows a pad after it sends input, so an empty
    list here just means nothing was touched yet.

    Best effort and never fatal: a diagnostic, not a feature.
    """
    profile = provider_profile(provider)
    marker = profile.get("page_match") or (url.split("/")[2] if "//" in url else url)
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target or not target.get("webSocketDebuggerUrl"):
        return
    try:
        seen = cdp.evaluate(target["webSocketDebuggerUrl"], """
            JSON.stringify([...navigator.getGamepads()].map(
                (g, i) => g ? {i, id: g.id, axes: g.axes.length,
                               buttons: g.buttons.length} : {i, id: null}))
        """)
    except cdp.CDPError as exc:
        log(f"could not read the page's gamepads: {exc}")
        return
    log(f"browser gamepads: {seen}")


def hold_platform_override(url: str, provider: str = ""):
    """Make the page believe it is on a supported OS, for the whole session.

    Luna has two gates that read different things. "Unsupported Browser" reads
    the User-Agent string, which the --user-agent flag settles. "Unsupported OS"
    reads navigator.platform / userAgentData, which that flag does not touch, so
    the flag alone lands you straight in the second gate. This closes it.

    Returns the held socket, which the caller must keep alive (see
    cdp.hold_platform), or None when the provider wants nothing, which is every
    provider but Luna. Best effort: if it fails the user is no worse off.
    """
    profile = provider_profile(provider)
    user_agent = profile.get("user_agent")
    if not user_agent:
        return None

    marker = profile.get("page_match") or (url.split("/")[2] if "//" in url else url)
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target or not target.get("webSocketDebuggerUrl"):
        log(f"page never appeared for the platform override (looked for {marker!r})")
        return None

    held = cdp.hold_platform(target["webSocketDebuggerUrl"], user_agent,
                             profile.get("platform", "Windows"))
    wants_shim = profile.get("startup_js") == "NEUTRALISE_APP_HANDOFF_JS"
    if wants_shim:
        cdp.add_startup_script(held, NEUTRALISE_APP_HANDOFF_JS)
    cdp.add_startup_script(held, VIRTUAL_GAMEPAD_JS)
    cdp.reload_page(held)
    log("injected virtual gamepad for this session")
    # add_startup_script turned the Page domain on, so the browser now pushes
    # events at this socket. Drain them or it wedges.
    if True:
        cdp.drain_in_background(held)
    log(f"{profile['label']}: reporting {profile.get('platform', 'Windows')} "
        "for this session")
    return held


def _click_fullscreen_if_needed(ws_url, timeout=15):
    """Wait for a fullscreen prompt and click it. Best effort."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = cdp.evaluate(ws_url, FULLSCREEN_CHECK_JS, timeout=5)
            if r and r != "none":
                hit = json.loads(r)
                cdp.dispatch_click(ws_url, hit["x"], hit["y"])
                log(f"clicked:{hit['text']}")
                return
        except Exception:
            pass
        time.sleep(1.5)


def try_autoplay(url: str, provider: str = "") -> None:
    """Press Play once the game page is up. Best effort — never fatal.

    If anything here fails the user just presses Play themselves, same as before.
    """
    profile = provider_profile(provider)
    click_js = CLICK_PLAY_JS % {
        "words": json.dumps(PLAY_WORDS),
        "deny": json.dumps(DENY_WORDS),
        "deny_phrases": json.dumps(DENY_PHRASES),
        "selectors": json.dumps(profile["selectors"]),
    }

    # How to spot the game's page. The host we asked for is the right default
    # (Xbox localises by path, so its host stays put). A provider that redirects
    # across country domains sets its own marker, see amazon_luna above.
    host_marker = url.split("/")[2] if "//" in url else url
    marker = profile.get("page_match") or host_marker
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target:
        log(f"game page never appeared (looked for {marker!r}); "
            "leaving Play to the user")
        return

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return

    deadline = time.monotonic() + CLICK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if cdp.evaluate(ws_url, IS_STREAMING_JS):
                log("already streaming")
                return

            # Stop early when the profile is not signed in: clicking has nothing
            # to find, and the full timeout would bury the one message the user needs.
            if cdp.evaluate(ws_url, profile["signed_out"]):
                log(f"not signed in to {profile['label']} in this browser.")
                log("Sign in on this page — the profile is kept, "
                      "so this is a one-off. Games launch straight in afterwards.")
                return

            result = cdp.evaluate(ws_url, click_js)
            if isinstance(result, str):
                try:
                    hit = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    hit = None
                if isinstance(hit, dict) and "clicked" in hit:
                    cdp.dispatch_click(ws_url, hit["x"], hit["y"])
                    log(f"clicked:{hit['clicked']}")
                    _click_fullscreen_if_needed(ws_url)
                    return
        except cdp.CDPError as exc:
            # Page may still be loading, or navigating between documents.
            log(f"waiting for Play ({exc})")
        time.sleep(1.5)

    # We never accept a consent dialog for the user. A fresh profile shows a
    # cookie or terms dialog, and clicking an agreement for someone is not our
    # business, so we say so instead.
    log(f"Play not found on {profile['label']}. If a cookie or "
          "terms dialog is showing, accept it once and press Play.")

    # Then say what WAS on the page. Every provider names this button
    # differently and changes it without warning, so listing the candidates here
    # turns the next failure into a log line that names the button we missed.
    try:
        labels = cdp.evaluate(ws_url, LIST_CANDIDATES_JS)
        if labels:
            log("clickable controls on the page were: "
                + " | ".join(str(x) for x in labels[:12]))
    except Exception:
        pass


def active_gamepads() -> list:
    """Virtual gamepads Steam Input made, as /dev/input/event* paths.

    One per connected controller. The browser reads these for the Gamepad API.
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
        # The NAME is the whole point of logging these. Steam renumbers its
        # virtual pads every session, so an event path says nothing about which
        # controller it is; the slot order is what decides who is player one.
        name = ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        pads.append(f"{name} [/dev/input/{handler}]" if name
                                    else f"/dev/input/{handler}")
    return pads


def wait_for_gamepad(timeout: float = 6.0, settle: float = 1.0) -> list:
    """Wait until Steam Input has FINISHED making its virtual pads.

    The browser reads input devices once at startup and udev hotplug does not
    reach it inside the flatpak sandbox, so a pad that shows up later is never
    seen. Returning on the first pad was wrong with two controllers: Steam makes
    them a fraction apart, so we could start the browser before pad 1 existed and
    the second controller was dead all session.

    So wait for the set to stop GROWING, not just to become non-empty: once it is
    unchanged for `settle` seconds Steam is done. Returns whatever exists at
    `timeout` if pads keep churning, and [] if none appear.
    """
    deadline = time.monotonic() + timeout
    pads: list = []
    last_change = None

    while time.monotonic() < deadline:
        current = active_gamepads()
        if current != pads:
            pads = current
            last_change = time.monotonic()
        elif pads and last_change is not None \
                and time.monotonic() - last_change >= settle:
            return pads
        time.sleep(0.3)
    return pads

def _clear_session_restore(profile: str) -> None:
    """Remove Chrome's session restore files so a killed browser doesn't ask to
    recover tabs next launch. The Deck closes the game to kill the browser, an
    unclean exit, so without this every launch shows "Restore pages?"."""
    default_dir = os.path.join(profile, "Default")
    sessions_dir = os.path.join(default_dir, "Sessions")
    if os.path.isdir(sessions_dir):
        for name in os.listdir(sessions_dir):
            try:
                os.remove(os.path.join(sessions_dir, name))
            except OSError:
                pass
    # Mark the exit clean in Preferences so Chrome hides the restore bubble
    prefs_path = os.path.join(default_dir, "Preferences")
    if os.path.isfile(prefs_path):
        try:
            with open(prefs_path, encoding="utf-8") as f:
                prefs = json.load(f)
            profile_data = prefs.get("profile", {})
            if profile_data.get("exit_type") != "Normal":
                profile_data["exit_type"] = "Normal"
                prefs["profile"] = profile_data
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f)
        except (json.JSONDecodeError, OSError):
            pass


def _kill_browser(profile: str) -> None:
    """Close only OUR kiosk browser, matched by its own user-data-dir. The
    browser runs in a flatpak sandbox, so killing the launcher's child leaves it
    alive holding the gamepad - that is the orphan that broke the Deck's internal
    controller. A blunt `flatpak kill` would reach it but also every other Edge
    window the user has open, so match on the dedicated profile instead.

    An EXACT argv-element match, not a substring: the website tile's profile is
    this one with a `-web` suffix, and a substring match would take it down too.
    Scans /proc so there is no shell regex to mis-escape. Never raises.
    """
    marker = ("--user-data-dir=" + profile).encode()
    me = os.getpid()
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(path, "rb") as handle:
                argv = handle.read().split(b"\x00")
        except OSError:
            continue                       # the process went away; fine
        if marker not in argv:
            continue
        try:
            pid = int(path.rsplit("/", 2)[1])
        except (IndexError, ValueError):
            continue
        if pid == me:
            continue                       # never signal the launcher itself
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def main() -> int:
    argv = sys.argv[1:]
    autoplay = "--no-autoplay" not in argv
    kiosk = "--no-kiosk" not in argv

    provider = ""
    if "--provider" in argv:
        index = argv.index("--provider")
        if index + 1 < len(argv):
            provider = argv[index + 1]
            argv = argv[:index] + argv[index + 2:]

    game_id = ""
    if "--game-id" in argv:
        index = argv.index("--game-id")
        if index + 1 < len(argv):
            game_id = argv[index + 1]
            argv = argv[:index] + argv[index + 2:]

    args = [a for a in argv if not a.startswith("--")]
    # The URL is optional now and usualy absent: a game tile carries --game-id
    # and we resolve from the gateway. Only an old shortcut and the website tile
    # still pass one positionally.
    if len(args) < 2:
        print(__doc__)
        return 2

    app_id, profile = args[0], args[1]
    url = args[2] if len(args) > 2 else ""
    os.makedirs(profile, exist_ok=True)

    log(f"--- launch: game={game_id or '?'} provider={provider or '?'} "
        f"browser={app_id}")

    url, source = resolve_launch(game_id, url, provider)
    log(f"launch URL from {source}")

    if not url:
        log("no launch URL: the gateway did not answer and nothing is stored "
            f"for game {game_id or '?'}. Sync once while the gateway is up.")
        return 4

    # Last gate before a browser opens. resolve_launch already checks the
    # gateway's answer, but the stored fallback reaches here too. A game that
    # does not start is a complaint; a fake fullscreen sign-in is a stolen account.
    is_page = provider in WEBSITE_PROVIDERS
    if not url_is_acceptable(url, provider):
        log(f"REFUSED: {url!r} is not somewhere a "
            f"{'website tile' if is_page else 'game'} may open "
            f"(source: {source}). Not launching.")
        return 3

    _clear_session_restore(profile)
    pads = wait_for_gamepad()
    if len(pads) > 1:
        # Known issue: with several controllers the browser can bind to the idle
        # virtual pad and see no gamepad at all. Seen on hardware. Logged so it
        # can be diagnosed.
        log(f"{len(pads)} virtual gamepads: {pads}. "
              "If the controller does not work in-game, disconnect the extra one.")
    elif not pads:
        log("no virtual gamepad yet; controller may not work")

    if is_page:
        profile = website_profile(profile)
        os.makedirs(profile, exist_ok=True)
        _clear_session_restore(profile)
        log(f"opening a website in its own profile: {profile}")
        code = subprocess.Popen(build_website_command(app_id, profile, url)).wait()
        log(f"--- website closed (browser exit {code})")
        return code

    browser = subprocess.Popen(build_browser_command(
        app_id, profile, url, kiosk,
        user_agent=provider_profile(provider).get("user_agent", "")))

    # When Steam closes the game it SIGTERMs this launcher. Take the kiosk
    # browser down with us - otherwise the flatpak app lives on and keeps the
    # gamepad, which is exactly how the Deck's internal controller stopped
    # working. Handlers, not just a finally, because SIGTERM skips finally.
    def _on_term(_signum, _frame):
        _kill_browser(profile)
        os._exit(0)
    for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(_sig, _on_term)
        except (ValueError, OSError):
            pass

    # Before autoplay and regardless of it: the OS gate blocks a hand-pressed
    # Play too. `held` is not unused: it is the open socket, and the override
    # lives exactly as long as this reference does.
    held = None
    try:
        held = hold_platform_override(url, provider)
    except Exception as exc:  # never let automation break the launch
        log(f"platform override failed, continuing: {exc}")

    if autoplay:
        try:
            try_autoplay(url, provider)
        except Exception as exc:  # never let automation break the launch
            log(f"autoplay failed, continuing: {exc}")

    try:
        wake_gamepads(url, provider)
    except Exception as exc:
        log(f"gamepad wake failed, continuing: {exc}")

    try:
        log_browser_gamepads(url, provider)
    except Exception as exc:          # diagnostics never break a launch
        log(f"gamepad diagnostic failed, continuing: {exc}")

    # In the background: it waits for a first frame and a queue can be minutes.
    # the browser is already up, and a daemon thread won't outlive the session.
    try:
        threading.Thread(target=_safely, args=(log_stream_resolution, url, provider),
                         name="stream-stats", daemon=True).start()
    except Exception as exc:
        log(f"stream diagnostic failed to start, continuing: {exc}")

    started = time.monotonic()
    code = browser.wait()
    # Even on a clean exit, a sandboxed renderer can linger; make sure none of
    # ours is left holding the controller.
    _kill_browser(profile)
    if held is not None:
        held.close()
    log(f"--- session ended after {int(time.monotonic() - started)}s "
        f"(browser exit {code})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
