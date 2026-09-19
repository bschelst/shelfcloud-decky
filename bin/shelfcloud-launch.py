#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Launch one cloud game and press the provider's Play button.

This is what a Shelfcloud Steam shortcut actually runs. It exists because the
provider's streaming URL contains a session id minted server-side when Play is
pressed, so the game page cannot be linked to directly — something has to click.

Steam launches this, not the plugin, so the automation has to live here rather
than in the plugin backend.

Usage:
    shelfcloud-launch.py <browser_app_id> <profile_dir> [url]
                         [--game-id N] [--provider KEY] [--no-autoplay] [--no-kiosk]

A game tile passes NO url. --game-id is what resolves a launch: the gateway is
asked where the game goes right now, so a changed URL or provider preference
takes effect without re-syncing, and the launch can be counted. When the
gateway does not answer, the offline URL is read from synced.json by game id.

It lives there rather than on this command line because Steam shows launch
options verbatim in a game's properties, and a provider deep link is not
something anyone needs to read there.

[url] is still accepted, for the website tile (which has no game id) and for
shortcuts written before the URL moved off the command line.

Exits when the browser exits, so Steam shows the game as running for the whole
session and the tile's playtime is accurate.
"""

import ipaddress
import json
import os
import ssl
import subprocess
import threading
import sys
import urllib.parse
import urllib.request
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import browsers  # noqa: E402
import weburl  # noqa: E402
import cdp  # noqa: E402
from autoplay import (  # noqa: E402
    CLICK_PLAY_JS, PLAY_WORDS, DENY_WORDS, DENY_PHRASES,
    IS_STREAMING_JS, LIST_CANDIDATES_JS, PROVIDERS, provider_profile,
)
from luna_shims import (  # noqa: E402
    NEUTRALISE_APP_HANDOFF_JS, VIRTUAL_GAMEPAD_JS, FULLSCREEN_CHECK_JS,
)

CDP_PORT = 9223

# Identifies this launcher to the gateway. It must be SOMETHING: the proxy in
# front of the gateway rejects urllib's default with 403.
USER_AGENT = "Shelfcloud-Launcher/0.1 (+https://github.com/bschelst/shelfcloud)"
PAGE_TIMEOUT = 45.0
CLICK_TIMEOUT = 60.0

# Where this plugin's gateway lives. Fixed, and https.
#
# It used to be read from settings.json, which holds whatever address the
# plugin was last pointed at — on this developer's Deck, a plaintext
# http://192.168.x.x:8000. Every launch then sent the bearer token across the
# LAN in clear and accepted a launch URL from whoever answered that port. The
# plugin backend already ignores that setting and uses its own constant; the
# launcher disagreeing with it is what kept the hole open.
#
# Must match DEFAULT_GATEWAY in main.py — tests/unit/test_gateway_default.py
# checks that they agree.
GATEWAY = "https://shelfcloud.cc"

# Where the gateway used to live. Still serving, and still allowed as a
# launch target during the changeover; see ALLOWED_LAUNCH_DOMAINS.
LEGACY_HOST = "shelfcloud.schelstraete.org"

# The only places a game is allowed to launch.
#
# Checked HERE, on the device, not merely on the gateway. The gateway is a
# server, servers get compromised, and a launch URL it returns is opened in a
# kiosk-mode browser carrying live Microsoft, NVIDIA and Boosteroid sessions —
# with no address bar and no tab strip to show where it went. A phishing page
# in that window is indistinguishable from the real sign-in.
#
# Every launch URL in the production catalog is https on one of these hosts, so
# this costs nothing in practice.
#
# Luna is pinned to the FULL host, not to "amazon.com". The entry below is a
# suffix rule, so "amazon.com" would admit every host Amazon serves — retail
# pages, seller-controlled content, arbitrary S3-backed subdomains — to a
# fullscreen browser holding the user's provider sessions. "luna.amazon.com" is
# the only Amazon host a Shelfcloud launch ever needs.
#
# That host is also why the catalog stores luna.amazon.com links rather than the
# play.amazon.<tld> URL Luna's own API returns: one host to allow instead of a
# dozen country variants, and it redirects each user to their own Luna domain.
ALLOWED_LAUNCH_DOMAINS = (
    "geforcenow.com",     # play.geforcenow.com
    "boosteroid.com",     # cloud.boosteroid.com
    "xbox.com",           # www.xbox.com
    "luna.amazon.com",    # Amazon Luna — full host on purpose, see above
    # Our own site. The plugin makes a "Shelfcloud (website)" tile so the
    # catalogue can be browsed under Steam Input — Steam's built-in browser
    # forwards no controller input, so the d-pad is dead there.
    #
    # It was missing, so that tile could never launch: every URL goes through
    # this check, the gateway's own host was not on the list, and the launcher
    # refused its own website with exit 3. Nothing said so on screen — the tile
    # simply did nothing.
    #
    # DERIVED from GATEWAY rather than typed again, so the two cannot drift.
    # Admitting it grants nothing new: this is the host the launcher already
    # asks where every game goes, so trusting it to answer and refusing to open
    # it was the inconsistent half.
    urllib.parse.urlsplit(GATEWAY).hostname or "",
    # The previous home, kept while devices catch up.
    #
    # A tile's address is resolved by the gateway at launch, so the moment the
    # published Shelfcloud page moves to the new domain, a Deck still running
    # an older plugin is handed an address its allowlist does not know — and a
    # Deck on the new plugin would refuse the old one. Allowing both means the
    # tile works whichever side of the update a device is on. Drop it once
    # every device has been updated.
    LEGACY_HOST,
)


def _ssl_context():
    """Verified TLS, using certifi's CA bundle if available.

    The launcher runs as a standalone process (not inside Decky's sandbox),
    so it cannot import Decky's helpers module. But certifi is installed by
    Decky and reachable on the system path, so we use it directly.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def is_allowed_launch_url(url) -> bool:
    """Is this somewhere a cloud game may legitimately be played?

    https only: a launch carries provider session cookies, and http would both
    expose them and let anything on the path rewrite the page.

    The host must BE one of the allowed domains or sit beneath it. The dot in
    `"." + domain` is the whole check — without it "xbox.com.evil.com" ends
    with "xbox.com" and passes.

    Credentials in the authority are refused outright:
    "https://play.geforcenow.com@evil.com/" names the host evil.com while
    reading, at a glance, as though it does not.

    A URL containing a backslash, a tab or a newline is refused before it is
    parsed at all, because on those this checker and the browser DISAGREE about
    what the host is — and the browser is the one that opens the page.

    Python's urlsplit follows RFC 3986, where "\\" is an ordinary character.
    Chromium follows the WHATWG URL Standard, where "\\" ends the authority
    exactly like "/". So

        https://evil.com\\cloud.boosteroid.com/steal

    has host "evil.com\\cloud.boosteroid.com" here — which ends with
    ".boosteroid.com" and passes every check below — and host "evil.com" in the
    browser, which then opens an attacker's page in kiosk mode with the user's
    provider sessions. Tab, CR and LF are the same class of bug: a browser
    strips them from a URL before resolving it, so they can move the host
    boundary after this function has approved it.

    No legitimate provider URL contains any of them, so refusing is free.
    """
    if not url or not isinstance(url, str):
        return False
    # Before parsing, and on the RAW string: .strip() would remove a trailing
    # newline that is part of the attack.
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

# Where the plugin keeps its gateway address and credential. The launcher is
# started by Steam, not by the plugin, so it has to read them itself.
SETTINGS_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/settings.json")

# What the plugin remembers about every tile it made, keyed by game id. The
# offline launch URL lives here rather than in the Steam shortcut, because
# Steam displays launch options verbatim in a game's properties and a provider
# deep link is not something anyone needs to read there.
STATE_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/synced.json")
RESOLVE_TIMEOUT = 8


def stored_fallback(game_id: str, provider: str = "") -> str:
    """The offline launch URL for this game, or "" if there is not a usable one.

    Never raises: this runs on the path where the gateway has ALREADY failed,
    so an unreadable or malformed state file must degrade to "no fallback"
    rather than take the launch down with it.

    The URL is checked here as well as at the end of main(). This file is
    written by the plugin, but it is also just a file on disk — and it is read
    precisely when the gateway cannot be consulted to correct it.
    """
    if not game_id:
        return ""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        entry = state.get(str(game_id))
        url = (entry or {}).get("url")
    except Exception:
        return ""
    return url if isinstance(url, str) and url_is_acceptable(url, provider) else ""

# Steam runs this script and captures none of its output — stdout goes nowhere
# a person can read. That mattered: when a launch quietly used the stored URL
# instead of asking the gateway, there was no way to tell from the outside,
# because a fallback is indistinguishable from success as far as the user is
# concerned. The game still starts. Only the gateway's silence gives it away.
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOMEBREW_DIR = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
LOG_FILE = os.path.join(_HOMEBREW_DIR, "logs", "Shelfcloud", "shelfcloud-launch.log")

# Kept small on purpose. This lives on a Deck's internal storage and nothing
# rotates it, so it trims itself rather than growing without end.
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_BYTES = 128 * 1024


def log(message: str) -> None:
    """Write one timestamped line, to the log file and to stdout.

    Never raises: a launcher that dies because it could not log would be a
    worse bug than the one this exists to diagnose.
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


def resolve_launch(game_id: str, fallback_url: str, provider: str = "") -> tuple:
    """Ask the gateway where this game launches right now.

    The URL used to be frozen into the Steam shortcut at sync time, so a launch
    never reached the gateway: a URL that changed upstream stayed broken until
    the next sync, and a changed provider preference needed a re-sync. Asking at
    launch fixes both, and lets the gateway count what actually gets played.

    The frozen URL is kept as a fallback and that is not belt-and-braces: the
    gateway is a machine on the user's LAN, and a game must not become
    unplayable because it is asleep. A slow gateway must not delay a launch
    either, hence the short timeout.

    Every path out of here is logged with its reason. A fallback is invisible
    from the user's side — the game starts either way — so without a line
    saying which route was taken, "the launch was not recorded" and "the
    gateway was never asked" look identical from the server.
    """
    # The offline route, looked up before the gateway is tried so every path
    # below has something to fall back to. A shortcut written before the URL
    # moved off the command line still passes one positionally; that keeps
    # working, and is preferred only when local state has nothing.
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

    # The address is the constant above, NOT settings["gateway_url"]. That key
    # still exists in settings written by older builds and points at whatever
    # the plugin was last aimed at — here, a plaintext LAN address. Reading it
    # put the bearer token on the wire in clear and took a launch URL from
    # whoever answered that port.
    gateway = GATEWAY
    token = settings.get("api_token")
    if not token:
        log("no credential in settings — not signed in; using the stored link. "
            "This launch will not be counted.")
        return fallback_url, "not signed in"

    try:
        request = urllib.request.Request(
            f"{gateway}/api/me/launch/{game_id}",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # Without this, urllib sends "Python-urllib/3.x" and the
                # reverse proxy in front of the gateway answers 403 — measured:
                # every other User-Agent reaches the app and gets a normal 401
                # for a bad token, that one never arrives at all.
                #
                # The failure was invisible, which is why it lasted: the
                # launcher falls back to the stored URL and the game still
                # starts, so nothing looked wrong. It only meant no launch was
                # ever resolved live and none was ever counted. The plugin
                # backend has always sent a User-Agent; this one never did.
                "User-Agent": USER_AGENT,
            },
        )
        started = time.monotonic()
        # Verified TLS against the bundled CA bundle, the same one main.py
        # uses: Decky's embedded Python ships its own libssl and has been seen
        # missing roots that a Cloudflare-fronted host presents.
        with urllib.request.urlopen(request, timeout=RESOLVE_TIMEOUT,
                                    context=_ssl_context()) as response:
            payload = json.loads(response.read().decode())
        elapsed = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        # Distinguish "the gateway said no" from "the gateway is not there":
        # a 401 needs a re-login, a refused connection needs the box woken up.
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

    # Do not trust the gateway with where this device browses. A correct
    # gateway only ever returns one of the permitted provider hosts; one that
    # returns anything else is either compromised or answering for someone who
    # is, and either way this is the point at which it stops.
    if not url_is_acceptable(url, provider):
        log(f"gateway returned a URL this tile may not open "
            f"({str(url)[:120]!r}) — REFUSED, using the stored link")
        return fallback_url, "refused gateway url"

    log(f"gateway resolved game {game_id} -> {payload.get('name') or '?'} "
        f"via {payload.get('provider') or '?'} in {elapsed} ms")
    return url, f"gateway ({payload.get('provider')})"

# Where a page tile may go, and which providers get that rule instead of the
# five-host provider allowlist above. Both live in py_modules/weburl.py, which
# main.py imports too — the decision to make a shortcut and the decision to
# open it have to agree, and they only do if there is one copy of the rule.
WEBSITE_PROVIDERS = weburl.WEBSITE_PROVIDERS
is_allowed_website_url = weburl.is_allowed_website_url


def build_website_command(app_id: str, profile: str, url: str) -> list:
    """Chromium command line for a page, which is not the one a game gets.

    Three deliberate differences, and each is the reason the allowlist can be
    relaxed for these tiles at all:

    * A SEPARATE PROFILE. The game profile holds live Microsoft, NVIDIA,
      Boosteroid and Luna sessions. A page somebody added has no business
      within reach of those, and a separate --user-data-dir is what keeps it
      out — cookies, storage and saved passwords all live in that directory.

    * NO REMOTE DEBUGGING PORT. A game window opens one so the launcher can
      press Play through CDP. That port is unauthenticated and accepts commands
      from anything that can reach it, including script in the page it is
      debugging; it is worth it for a fixed set of provider sites and is not
      worth it for an arbitrary one. Nothing here needs it, so it is absent
      rather than merely unused.

    * NO KIOSK. Kiosk hides the address bar, and the address bar is how a
      person can tell where they actually are. That is exactly the check the
      allowlist performs for game tiles, handed back to the user for pages the
      catalogue cannot vouch for.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        "--window-size=1280,800",
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
    """Which of the two gates applies, decided by what kind of tile this is.

    One function so that the three places a URL is checked — the gateway's
    answer, the link stored on disk, and the last step before a browser opens —
    cannot end up disagreeing about which rule a page is held to.
    """
    if provider in WEBSITE_PROVIDERS:
        return is_allowed_website_url(url)
    return is_allowed_launch_url(url)


def build_browser_command(app_id: str, profile: str, url: str, kiosk: bool = True,
                          user_agent: str = "") -> list:
    """Chromium command line for one session.

    Kiosk is right for streaming and wrong for signing in: a Microsoft or
    NVIDIA login can bounce through pages that need a visible tab strip and a
    way back, and a kiosk window offers neither.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={CDP_PORT}",
        "--window-size=1280,800",
        "--class=shelfcloud",
        *browsers.BASE_FLAGS,
        *([f"--user-agent={user_agent}"] if user_agent else []),
        *(["--kiosk"] if kiosk else []),
        url,
    ]


# Luna asks the desktop app to take over, and on a Deck there is no desktop app.
#
# Read off Luna's own bundle. AppLauncher builds "luna://play.amazon<tld>/..."
# and hands it to launchProtocol, which for Chrome ends in:
#
#     function openUriWithHiddenFrame(uri, fail, success) {
#         const timer = setTimeout(() => { fail(); ... }, 500);
#         let frame = document.querySelector("#hiddenIframe")
#                     || _createHiddenIframe(document.body, "about:blank");
#         ... window "blur" -> success ...
#         frame.contentWindow.location.href = uri;
#     }
#
# So it pokes the scheme at a hidden iframe and reads 500ms of silence as "app
# not installed". Chromium turns that poke into the "This site is trying to
# open xdg-open" prompt, which sits on top of the game until it is dismissed.
#
# There is NO browser flag for this — external-protocol handling is deliberately
# not switchable, and the enterprise policy that can block it (URLBlocklist)
# would mean writing a system-wide policy file as root on the user's device.
#
# So: supply #hiddenIframe ourselves, with a contentWindow whose location.href
# setter does nothing. Luna's querySelector finds ours, skips making its own,
# pokes the stub, hears nothing, and takes its own "not installed" branch —
# which calls .catch(() => this.provision()) and streams in the browser.
#
# That is not a trick to defeat a check. It is the honest answer: there is no
# Luna desktop app on this machine, and the only thing being suppressed is a
# prompt asking the user to confirm that.
#
# An own property shadows the prototype getter, which is why this works at all:
# Location.href is [Unforgeable], so the real one can never be overridden.


def wake_gamepads(url: str, provider: str = "") -> None:
    """No-op. The virtual gamepad override broke real gamepad detection on
    Boosteroid and GeForce NOW by masking the native getGamepads() result.
    Luna's injection lives in hold_platform_override and is unaffected."""
    pass



def log_browser_gamepads(url: str, provider: str = "") -> None:
    """Record what the PAGE sees, which is the only view that decides anything.

    The launcher can list Steam's virtual pads all day; the provider's web
    player reads navigator.getGamepads() and generally drives whatever sits at
    index 0. If index 0 is the Deck's own controls, an external controller does
    nothing until Steam's order is changed — and Steam does not persist that
    order for these tiles, so it is changed again every launch.

    Chrome exposes a gamepad to a page only after that pad sends input, so an
    empty list here is not a fault: it means nothing had been touched yet, and
    it is worth knowing too.

    Best effort and never fatal: this is a diagnostic, not a feature.
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

    Luna has TWO gates, and they read different things:

      "Unsupported Browser" — reads the User-Agent STRING. The --user-agent
                              flag in build_browser_command settles this one.
      "Unsupported OS"      — reads navigator.platform / navigator.userAgentData,
                              which that flag does not touch. Measured with the
                              flag on: platform "Linux x86_64", uaData "Linux".

    So the flag alone gets past the first gate and straight into the second,
    which is exactly what happened. This closes the second one.

    Returns the held socket, which the caller must keep alive — see
    cdp.hold_platform. Returns None when the provider asks for nothing, which
    is every provider but Luna.

    Best effort, like try_autoplay: if this fails the user is no worse off than
    before it existed, and a game that does not stream is not worth aborting a
    launch over.
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
    # add_startup_script enables the Page domain, which pushes events into the
    # socket. Drain them or the socket wedges.
    if True:
        # add_startup_script had to enable the Page domain, so from here on the
        # browser is pushing events at this socket. Drain them or it wedges.
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

    If anything here fails the user simply presses Play themselves, which is the
    behaviour they would have had anyway. Nothing is worth killing the session
    over.
    """
    profile = provider_profile(provider)
    click_js = CLICK_PLAY_JS % {
        "words": json.dumps(PLAY_WORDS),
        "deny": json.dumps(DENY_WORDS),
        "deny_phrases": json.dumps(DENY_PHRASES),
        "selectors": json.dumps(profile["selectors"]),
    }

    # How to recognise the game's page once the browser has it open.
    #
    # The host we asked for is the right default — Boosteroid, GeForce NOW and
    # Xbox all stay on it (Xbox localises by PATH, /nl-BE/play/..., so its host
    # never changes). A provider that redirects across country DOMAINS declares
    # its own marker instead; see amazon_luna above.
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

            # Stop early when the profile simply is not signed in. Clicking has
            # nothing to find, and spending the full timeout on it buries the
            # one message the user actually needs in a minute of silence.
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

    # Nothing is auto-accepted on the user's behalf. Both providers show a
    # consent dialog on a fresh profile — Microsoft's cookie banner, NVIDIA's
    # "AGREE & CONTINUE" — and clicking an agreement for someone is not the
    # launcher's business, so it says so instead.
    log(f"Play not found on {profile['label']}. If a cookie or "
          "terms dialog is showing, accept it once and press Play.")

    # Then say what WAS on the page.
    #
    # Every provider labels this button differently and changes it without
    # warning, so "not found" on its own costs a round trip: connect to the
    # Deck, hope the page is still open, and read the DOM. Listing the
    # candidates here turns the next failure into a log line that names the
    # button we should have matched.
    try:
        labels = cdp.evaluate(ws_url, LIST_CANDIDATES_JS)
        if labels:
            log("clickable controls on the page were: "
                + " | ".join(str(x) for x in labels[:12]))
    except Exception:
        pass


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
        # The NAME is the whole point of logging these. Steam renumbers its
        # virtual pads every session — the same controller was event19 one
        # night and event18 the next — so a path says nothing about WHICH
        # controller it is, while "pad 0" / "pad 1" is Steam's slot order, and
        # slot order is what decides who is player one.
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

    The browser enumerates input devices once, at startup, and udev hotplug
    events do not reach it reliably inside the flatpak sandbox — so a pad that
    appears after it starts may never be noticed at all.

    This used to return on the first pad it saw, which is the wrong moment when
    two controllers are connected: Steam creates them a fraction apart, so
    returning on pad 0 could launch the browser before pad 1 existed, and the
    external controller was then invisible for the whole session. The symptom
    is indistinguishable from the controller being in the wrong slot, which is
    part of why that was hard to pin down.

    So wait for the set to stop GROWING rather than to become non-empty: once
    the list has been unchanged for `settle` seconds, Steam is done. Costs
    about a second on a launch that already takes several.

    Returns whatever exists at `timeout` if pads keep churning, and [] if none
    ever appear — both are honest answers, and the caller only logs them.
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
    recover tabs on the next launch. The Deck kills the browser by closing the
    game, which is always an unclean exit — without this, every launch shows
    "Restore pages?" with the previous session's tabs."""
    default_dir = os.path.join(profile, "Default")
    # Remove the Session and Tabs files
    sessions_dir = os.path.join(default_dir, "Sessions")
    if os.path.isdir(sessions_dir):
        for name in os.listdir(sessions_dir):
            try:
                os.remove(os.path.join(sessions_dir, name))
            except OSError:
                pass
    # Mark the exit as clean in Preferences so Chrome doesn't show the bubble
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
    # The URL is OPTIONAL now, and normally absent: a game tile carries
    # --game-id and the launcher resolves the URL from the gateway, falling
    # back to synced.json. Two callers still pass one positionally — a shortcut
    # written before that change, and the website tile, which has no game id to
    # resolve from.
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

    # Last gate before a browser opens. resolve_launch already refuses a URL
    # the gateway should not have sent, but the stored fallback reaches here
    # too — written by an earlier sync and sat on disk ever since. Refusing to
    # launch is the right outcome: a game that does not start is a complaint,
    # and a fullscreen page pretending to be a provider sign-in is a stolen
    # account.
    is_page = provider in WEBSITE_PROVIDERS
    if not url_is_acceptable(url, provider):
        log(f"REFUSED: {url!r} is not somewhere a "
            f"{'website tile' if is_page else 'game'} may open "
            f"(source: {source}). Not launching.")
        return 3

    _clear_session_restore(profile)
    pads = wait_for_gamepad()
    if len(pads) > 1:
        # Known issue: with several controllers connected the browser can bind
        # to the idle virtual pad and report no gamepad at all. Verified on
        # hardware — 4,473 events on the live pad, zero on the one Chrome
        # opened. Logged so it is diagnosable rather than mysterious.
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

    # Before autoplay, and regardless of it: the OS gate blocks a Play the user
    # presses by hand just as surely as one the launcher presses.
    #
    # `held` is not an unused variable. It is the open socket, and the override
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

    started = time.monotonic()
    code = browser.wait()
    if held is not None:
        held.close()
    log(f"--- session ended after {int(time.monotonic() - started)}s "
        f"(browser exit {code})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
