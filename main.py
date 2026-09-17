# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Shelfcloud — Decky plugin backend.

The plugin is a sync agent, not a game browser. Games are chosen on the web,
where there is a real screen and keyboard; this keeps the Deck's Steam library
matching that choice.

Responsibilities split by what each half can reach:
  * Python (here) — network, credentials, disk state, and the poll loop.
  * Frontend      — anything touching SteamClient, which only exists there.

So this module decides *what* the library should contain and emits an event;
the frontend makes the shortcuts actually match.

Stdlib only: Decky's Python environment does not guarantee third-party packages.
"""

import asyncio
import base64
import json
import os
import random
import shlex
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import decky

import browsers
import shortcuts


def _ssl_context() -> ssl.SSLContext:
    """An SSL context that trusts the bundled Mozilla CA bundle.

    Decky's embedded Python ships its own libssl and may not include every
    root certificate the system has. The GTS Root R4 (Google Trust Services)
    that Cloudflare-proxied sites present is one that has been missing,
    causing CERTIFICATE_VERIFY_FAILED on an otherwise valid chain.

    The bundled cacert.pem (from curl.se, the same file certifi ships) is
    loaded on top of whatever the system already trusts.
    """
    ctx = ssl.create_default_context()
    bundle = os.path.join(os.path.dirname(__file__), "py_modules", "cacert.pem")
    if os.path.isfile(bundle):
        ctx.load_verify_locations(bundle)
    else:
        # Loudly, because the silent version of this cost an evening. The
        # install script copied py_modules/*.py and left the .pem behind, so
        # the bundle was in the repo, in the staging directory, and absent from
        # the plugin — and the only symptom was CERTIFICATE_VERIFY_FAILED on
        # every request, with the same request from the Deck's own python
        # working fine.
        decky.logger.error(
            "CA bundle missing at %s — TLS will fall back to the loader's own "
            "trust store, which does not carry the root our gateway presents. "
            "Reinstall the plugin; the installer must copy all of py_modules, "
            "not just *.py.", bundle)
    return ctx

SSL_CTX = _ssl_context()


def _shell_join(parts: List[str]) -> str:
    """Build a Steam shortcut's launch options as one shell-safe string.

    Steam runs a shortcut through `/bin/sh -c "<exe> <options>"`, so every
    argument has to survive a round trip through the shell. Plain `" ".join`
    does not, and the way it fails is genuinely nasty:

    a GeForce NOW URL ends `?game-id=<uuid>&lang=en_US`, and that unquoted `&`
    is a shell operator. `sh` backgrounds everything before it and treats the
    rest as a second command. The launcher then receives a URL truncated at the
    ampersand and — the part that actually hurt — no `--game-id` and no
    `--provider` at all, because those land in the discarded half. The game
    still starts from the truncated URL, so nothing looks wrong from the Deck;
    it just silently stops asking the gateway, which means no launch is ever
    resolved live and none is ever counted.

    Quoting is therefore not tidiness here. Do not simplify this back.
    """
    return " ".join(shlex.quote(part) for part in parts)


DEFAULT_GATEWAY = "https://shelfcloud.schelstraete.org"

# No install has been started. Also the shape the frontend reads, so it can
# rely on the keys existing rather than guarding each one.
IDLE_INSTALL = {
    "running": False, "app_id": None, "label": None,
    "message": "", "error": None, "done": False,
}
REQUEST_TIMEOUT = 20
SYNC_EVENT = "shelfcloud/library"

def _hardware_label() -> str:
    """What kind of machine this is, for naming the device on the gateway.

    The gateway names a new device from its User-Agent, and a browser's UA says
    enough to guess with. This plugin's does not — every install would register
    as "Shelfcloud plugin", which is no better than the "Unnamed device" it
    replaces once someone owns a Deck and a Steam Machine.

    The DMI product name is what distinguishes them. Valve ships two codenames
    and neither means anything to a person, so they are translated; anything
    else is reported as-is, which covers a Steam Machine or a desktop running
    Bazzite without needing to know their names in advance.
    """
    known = {"Jupiter": "Steam Deck", "Galileo": "Steam Deck OLED"}
    try:
        with open("/sys/class/dmi/id/product_name", encoding="utf-8") as handle:
            product = handle.read().strip()
    except Exception:
        product = ""
    if product in known:
        return known[product]
    # Placeholders real boards ship with, which name nothing.
    if product and product.lower() not in ("to be filled by o.e.m.", "system product name",
                                           "default string", "none"):
        # ';' and ')' would break out of the User-Agent comment this goes into.
        return product.replace(";", " ").replace(")", " ")[:48]
    try:
        return os.uname().nodename[:48] or "Shelfcloud plugin"
    except Exception:
        return "Shelfcloud plugin"


# Artwork is fetched here rather than in the browser; see fetch_artwork.
#
# The hardware goes in the comment field so the gateway can name this device
# something a person recognises in a list.
ARTWORK_USER_AGENT = (
    f"Shelfcloud/0.1 ({_hardware_label()}; +https://github.com/bschelst/shelfcloud)")
ARTWORK_MAX_BYTES = 8 * 1024 * 1024

# How often to check the gateway for library changes, in minutes.
SYNC_INTERVAL_CHOICES = [1, 5, 10, 30, 60]
DEFAULT_SYNC_MINUTES = 5
# Re-read between ticks rather than caching, so a changed setting takes effect
# on the next tick instead of requiring a plugin reload.
MIN_SYNC_SECONDS = 30

# Spread each device's poll around the interval instead of landing exactly on
# it. With a fixed interval, devices that start together stay together forever:
# a plugin update rolled out to a fleet starts every copy within seconds of the
# others, and they then knock on the gateway in lockstep for as long as they run.
#
# It matters most for the thing that makes every client act at once anyway — a
# bumped artwork revision re-applies art on every tile on every device. The
# gateway's artwork cache absorbs the repeat work, but only if the requests do
# not arrive as one wall.
SYNC_JITTER = 0.15

# Steam needs a moment before shortcuts can be added safely; adding them while
# the library is still loading is asking for trouble.
STARTUP_SETTLE_SECONDS = 20

# Ceiling on the extra random delay before the FIRST poll. A fleet updating
# together disperses across this window immediately rather than over several
# intervals — but capped, so a user on a 6-hour interval does not wait hours
# for their first sync.
STARTUP_SPREAD_MAX_SECONDS = 60


def next_poll_delay(base_seconds: float, rng=None) -> float:
    """Seconds to wait before the next poll, jittered around `base_seconds`.

    Pure and separate from the loop so it can be tested: a jitter bug is
    invisible in a single-device test — one client polling on a slightly wrong
    schedule looks exactly like one polling correctly — and only shows up as a
    thundering herd once there are many.
    """
    if rng is None:
        rng = random
    spread = base_seconds * SYNC_JITTER
    # Never below the floor: jitter must not become a way to poll faster than
    # the rate limit allows.
    return max(MIN_SYNC_SECONDS, base_seconds + rng.uniform(-spread, spread))

# How often to re-confirm the token with the gateway. Short enough that a
# revoked session is noticed quickly, long enough not to be chatty.
AUTH_RECHECK_SECONDS = 60

# How long to wait for a Steam sign-in, and how often to check.
LOGIN_TIMEOUT_SECONDS = 5 * 60
LOGIN_POLL_SECONDS = 2.5


def _settings_file() -> str:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def _state_file() -> str:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "synced.json")


def _icon_dir() -> str:
    """Where shortcut icons live on disk.

    Icons are not like the other artwork. The four library slots are handed to
    Steam as bytes and Steam stores its own copy; an icon is stored in
    shortcuts.vdf as a PATH, and Steam reads that file every time it draws the
    icon. Delete the file and the icon breaks — so this must be somewhere
    durable, NOT the plugin directory, which install-to-deck.sh overwrites.

    DECKY_PLUGIN_SETTINGS_DIR is the one place the installer is careful to
    preserve (it holds the API token), which makes it the right home.
    """
    path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "icons")
    os.makedirs(path, exist_ok=True)
    return path


def as_id(value: Any) -> Optional[int]:
    """An integer id out of JSON, or None if it is anything else.

    JSON promises nothing about types. An id can arrive as a string from a
    proxy that rewrites bodies, as a float from a language whose numbers are
    all floats, or as a dict from an error document served with the wrong
    content type. Everything downstream — a URL path, a dropdown key — wants
    an int or nothing at all, so the conversion happens once, here.

    bool is rejected before int because it is a SUBCLASS of int: without that
    line `True` quietly becomes the id 1, which is a real shelf.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def clean_shelf(entry: Any) -> Optional[Dict[str, Any]]:
    """One shelf fit to render, or None if there is nothing to select.

    Only a missing id drops the row, because an id is the whole of what a
    selection sends. A missing name is given one instead of being dropped: the
    device may be pointed AT this shelf, and a picker whose current value is
    not among its options draws as an empty control — which reads as the
    plugin being broken rather than as one shelf being odd.
    """
    if not isinstance(entry, dict):
        return None
    shelf_id = as_id(entry.get("id"))
    if shelf_id is None:
        return None
    name = entry.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else f"Shelf {shelf_id}"
    return {"id": shelf_id, "name": name, "games": as_id(entry.get("games")) or 0}


class Plugin:
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        # game_id -> {"app_id": int, "name": str, "launch": str}
        # Without this, a game removed on the web could never be found and
        # removed here, and re-syncing would create duplicate tiles.
        self._synced: Dict[str, Dict[str, Any]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._login_task: Optional[asyncio.Task] = None
        # The pairing code for a sign-in in progress, shown in the panel
        # so the person can read it while the browser asks for it.
        self._pending_login_code: str = ""
        self._last_error: Optional[str] = None
        self._last_sync: Optional[float] = None
        # Whether the gateway last accepted our token. A token can stop working
        # without the plugin doing anything, so "we have a string on disk" is
        # not the same as "we are signed in".
        self._auth_ok: bool = True
        self._auth_checked: float = 0.0
        self._version_cache: Optional[str] = None
        # Progress of a browser install, read by the panel through get_status
        # so it survives the panel being closed mid-download.
        self._install: Dict[str, Any] = dict(IDLE_INSTALL)
        self._install_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------- lifecycle

    async def _main(self):
        self._settings = await asyncio.to_thread(self._read_json, _settings_file())
        self._synced = await asyncio.to_thread(self._load_state)
        decky.logger.info("Shelfcloud ready (gateway=%s, tracked=%d)",
                          self._gateway(), len(self._synced))
        await self.apply_overrides()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _unload(self):
        if self._poll_task:
            self._poll_task.cancel()
        if self._login_task:
            self._login_task.cancel()
        decky.logger.info("Shelfcloud unloaded")

    async def _uninstall(self):
        icon_path = _icon_dir()
        if os.path.isdir(icon_path):
            for name in os.listdir(icon_path):
                try:
                    os.remove(os.path.join(icon_path, name))
                except OSError:
                    pass
            try:
                os.rmdir(icon_path)
            except OSError:
                pass
        decky.logger.info("Shelfcloud uninstalled")

    # ---------------------------------------------------------------- config

    @classmethod
    def _load_state(cls) -> Dict:
        """Which tiles this plugin has made — recovered from Steam if lost.

        This used to be a plain _read_json, which answers {} for a file that is
        missing OR unreadable. Reconcile works entirely from this map, so {}
        does not mean "no tiles", it means "I have never made any" — and every
        game then reads as desired-but-untracked and gets a BRAND NEW tile.

        The old tiles do not go away. They are untracked, so nothing cleans
        them up, and the user gets duplicates. Worse, a Steam shortcut's
        identity IS its app id: playtime, collections, artwork and the
        per-game controller configuration all hang off it. Recreating a tile
        silently abandons all of that on an app id nothing points at any more.

        Seen on a real device: a synced.json.bak containing exactly "{}".

        shortcuts.vdf is the authority on what Steam actually has, and every
        tile this plugin makes carries "--game-id N" in its launch options, so
        the file says which game each one belongs to. Rebuilding from it is
        both safer and more correct than trusting our own copy:

          - first run, no tiles          -> {} , which is right
          - state lost, tiles exist      -> the real app ids, so nothing is
                                            recreated and nothing is orphaned
          - tiles the user deleted       -> absent, so they stay deleted
        """
        state = cls._read_json(_state_file())
        if state:
            return state

        recovered = cls._recover_from_steam()
        if recovered:
            decky.logger.warning(
                "state file missing or unreadable; recovered %d tile(s) from "
                "shortcuts.vdf rather than recreating them", len(recovered))
        return recovered

    @staticmethod
    def _recover_from_steam() -> Dict:
        """game id -> {app_id}, read back out of Steam's own shortcuts.vdf.

        Only the app id is recovered, because only the app id is irreplaceable
        — name, exe and launch options are rewritten on every sync anyway.
        """
        try:
            found = shortcuts.shelfcloud_shortcuts()
        except Exception as exc:                      # never block startup
            decky.logger.warning("could not read shortcuts.vdf: %s", exc)
            return {}

        recovered: Dict = {}
        for entry in found:
            # setdefault: duplicates() documents that Steam's order is oldest
            # first, and the oldest tile is the one holding the playtime and
            # the controller layout.
            recovered.setdefault(str(entry["game_id"]), {"app_id": entry["app_id"]})
        return recovered

    @staticmethod
    def _read_json(path: str) -> Dict:
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_json(path: str, data: Dict) -> None:
        # Write-then-rename so a crash mid-write cannot leave unreadable state.
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

        # 0600, because settings.json holds the bearer token.
        #
        # Decky runs as root but drops to `deck` for plugin backends — ALL of
        # them, this one included. At the default 0644 any other installed
        # plugin could simply read this file and walk off with the credential,
        # no privilege escalation required. The mode is set on the temporary
        # file before the rename so the finished file is never briefly
        # world-readable.
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def _gateway(self) -> str:
        return DEFAULT_GATEWAY

    def _token(self) -> Optional[str]:
        return self._settings.get("api_token")

    async def _save_settings(self) -> None:
        await asyncio.to_thread(self._write_json, _settings_file(), self._settings)

    async def _save_state(self) -> None:
        await asyncio.to_thread(self._write_json, _state_file(), self._synced)

    # ------------------------------------------------------------------ http

    def _request(self, path: str, method: str = "GET", body: Optional[Dict] = None,
                 authenticated: bool = True) -> Any:
        url = self._gateway() + path
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", ARTWORK_USER_AGENT)
        request.add_header("Accept", "application/json")
        if data:
            request.add_header("Content-Type", "application/json")
        if authenticated:
            token = self._token()
            if not token:
                raise PermissionError("not signed in")
            request.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=SSL_CTX) as response:
                payload = response.read().decode()
                self._auth_ok = True
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and authenticated:
                self._forget_credentials()
                raise PermissionError("signed out — sign in again") from exc
            raise
        except urllib.error.URLError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in str(exc):
                decky.logger.error("SSL error reaching %s: %s", url, exc)
            raise

    # ------------------------------------------------------------------ auth

    async def start_login(self) -> Dict[str, Any]:
        """Begin sign-in. The frontend opens login_url via steam://openurl,
        because Game Mode has no browser of its own."""
        try:
            result = await asyncio.to_thread(
                self._request, "/auth/session?web=0", "POST", None, False
            )
            # The pairing code is what stops someone else finishing this
            # sign-in. It is kept HERE rather than only returned once, because
            # opening Steam's browser closes the panel — the user has to be
            # able to peek back at the panel to read the code while the browser
            # is asking for it, and a value that lived only in the panel's
            # state would be gone by then.
            self._pending_login_code = result.get("confirm_code") or ""

            # Wait for completion here, not in the panel: opening Steam's
            # browser closes the panel and would take the waiter with it.
            if self._login_task and not self._login_task.done():
                self._login_task.cancel()
            session_token = result.get("session_token")
            if not session_token:
                return {"ok": False, "error": "gateway did not return a session token"}
            self._login_task = asyncio.create_task(
                self._await_login(session_token))
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def _await_login(self, session_token: str) -> None:
        """Wait for the Steam sign-in to complete, then keep the credential.

        This has to live in the backend. Signing in opens Steam's browser,
        which closes the Quick Access panel — and the panel is a React
        component, so unmounting it killed the timer that was polling for the
        result. Steam authenticated the user correctly every time; nobody was
        left to collect the token, and the plugin stayed signed out.

        The same lesson as the background sync: anything that must outlive the
        panel cannot live in the panel.
        """
        deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(LOGIN_POLL_SECONDS)
            try:
                result = await asyncio.to_thread(
                    self._request, f"/auth/session/{session_token}", "GET", None, False
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                continue        # gateway asleep or mid-restart; keep waiting

            if not result or result.get("status") != "ok":
                continue

            # Paired. The code has served its purpose and should not linger
            # in the panel for the next person who opens it.
            self._pending_login_code = ""
            api_token = result.get("api_token")
            if not api_token:
                decky.logger.warning("Sign-in response missing api_token")
                continue
            self._settings["api_token"] = api_token
            self._settings["steam_id64"] = result.get("steam_id64")
            # A fresh credential invalidates any cached "not signed in" answer.
            self._auth_ok = True
            self._auth_checked = 0.0
            await self._save_settings()
            decky.logger.info("Signed in as %s", result.get("steam_id64"))
            # Populate the library straight away rather than waiting a tick.
            await decky.emit(SYNC_EVENT)
            return

        self._pending_login_code = ""
        decky.logger.info("Sign-in was not completed within the time limit")

    async def poll_login(self, session_token: str) -> Dict[str, Any]:
        """Progress of the sign-in the backend is waiting on.

        The backend does the claiming — a session token is single use, so two
        claimants would race and one would be told the link was already used.
        This only reports.
        """
        if self._token():
            return {"ok": True, "status": "signed_in",
                    "steam_id64": self._settings.get("steam_id64")}
        waiting = self._login_task is not None and not self._login_task.done()
        return {"ok": True, "status": "pending" if waiting else "idle"}

    def _forget_credentials(self) -> None:
        """Drop a credential the gateway no longer accepts."""
        self._settings.pop("api_token", None)
        self._settings.pop("steam_id64", None)
        self._auth_ok = False
        self._auth_checked = 0.0
        try:
            self._write_json(_settings_file(), self._settings)
        except OSError:
            pass   # in-memory state is already correct; disk catches up later

    async def logout(self) -> Dict[str, Any]:
        try:
            await asyncio.to_thread(self._request, "/api/me/logout", "POST", {})
        except Exception:
            pass  # local sign-out must work even when the gateway is unreachable
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        self._settings.pop("api_token", None)
        self._settings.pop("steam_id64", None)
        self._auth_ok = False
        self._auth_checked = 0.0
        await self._save_settings()
        return {"ok": True}

    # --------------------------------------------------------------- library

    @staticmethod
    def _artwork(game: Dict) -> Dict[str, Optional[str]]:
        """Artwork for each Steam library slot, as resolved by the gateway.

        Deliberately no lookups here: artwork is catalog data, resolved once
        server-side for every user, so the plugin only renders what it is given.
        Falls back to provider images for games the gateway could not resolve.
        """
        def url(*keys) -> Optional[str]:
            """First value that is actually a usable URL, else None.

            https only, at the point the value is first accepted. fetch_artwork
            checks again before anything is downloaded, but a plain-http URL
            would otherwise be baked into a persisted shortcut on the strength
            of a second, separate check staying correct forever.
            """
            for key in keys:
                value = game.get(key)
                if isinstance(value, str) and value.startswith("https://"):
                    return value
            return None

        return {
            "capsule": url("capsule_url"),
            "header": url("header_url"),
            "hero": url("hero_url", "banner_url"),
            "logo": url("logo_url"),
        }

    def _artwork_proxy(self, game: Dict) -> Dict[str, str]:
        """The same artwork, served back through the gateway.

        Steam applies custom art by being handed bytes, so the plugin fetches
        the image from inside Steam's client — a cross-origin request. Steam's
        own CDN sends `access-control-allow-origin: *`; SteamGridDB sends no
        CORS header at all, so the browser discards the response and every
        cloud-only game kept a blank tile with nothing logged. The gateway does
        send the header, so it can hand over what the CDN will not.
        """
        game_id = game.get("id")
        if not isinstance(game_id, int):
            return {}
        base = self._gateway().rstrip("/")
        return {slot: f"{base}/api/artwork/{game_id}/{slot}"
                for slot in ("capsule", "header", "hero", "logo")}

    def _to_desired(self, game: Dict) -> Optional[Dict]:
        """Turn a library entry into everything the frontend needs to make a tile.

        Every field is checked rather than indexed. This reads data from a
        network service, and one malformed entry used to raise straight out of
        the loop and abandon the whole sync — so a single odd row in the
        catalog could stop every other game from appearing. Anything that does
        not have what a Steam shortcut needs is skipped and counted.
        """
        if not isinstance(game, dict):
            return None

        game_id = game.get("id")
        name = game.get("name")
        if not isinstance(game_id, int) or not isinstance(name, str) or not name.strip():
            return None

        raw_providers = game.get("providers")
        providers = [p for p in raw_providers if isinstance(p, dict)] \
            if isinstance(raw_providers, list) else []
        # A route is only usable with somewhere to go and something to call it.
        providers = [
            p for p in providers
            # https only: this URL is written into a Steam shortcut and opened
            # later by the launcher, which checks the scheme and the host again.
            # Refusing here too keeps a plain-http link from ever being stored.
            if isinstance(p.get("launch_url"), str) and p["launch_url"].startswith("https://")
            and isinstance(p.get("provider"), str) and p["provider"]
        ]
        if not providers:
            return None

        chosen = game.get("chosen_provider")
        provider = next((p for p in providers if p["provider"] == chosen), providers[0])

        browser = browsers.resolve_browser(self._settings.get("browser"))
        if browser is None:
            return None

        launcher = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "shelfcloud-launch.py")
        profile = browsers.profile_dir(browser["app_id"])
        # NO URL on the command line.
        #
        # Steam shows launch options verbatim in a game's properties, so the
        # provider's deep link sat there in the interface for every tile — and
        # it is not something a person needs to read, or should have to.
        #
        # --game-id is what actually resolves a launch: the launcher asks the
        # gateway where the game goes right now, which keeps a changed URL or a
        # changed provider preference working without a re-sync and lets the
        # gateway count launches. The fallback for a gateway that is asleep now
        # lives in synced.json beside everything else this plugin remembers,
        # read by game id — off the command line and out of the interface.
        #
        # --provider stays: what a signed-out page looks like and where the
        # Play control lives differ per provider, and that has to be known
        # before any URL is resolved.
        options = _shell_join([
            launcher, browser["app_id"], profile,
            "--game-id", str(game_id),
            "--provider", provider["provider"],
        ])

        return {
            "game_id": game_id,
            # Recorded locally as the offline fallback, never put on the
            # command line.
            "url": provider["launch_url"],
            "name": name,
            "provider": provider["provider"],
            "exe": "/usr/bin/python3",
            "options": options,
            "artwork": self._artwork(game),
            "artwork_proxy": self._artwork_proxy(game),
        }

    async def get_website_launch(self) -> Optional[Dict[str, str]]:
        """Launch command for a Steam tile that opens the Shelfcloud website.

        Steam's own in-client browser forwards no controller input to web pages
        — verified on hardware: zero key events, zero gamepads, so the d-pad is
        dead there and no page-side code can help. Launching the site as a Steam
        shortcut instead puts it under Steam Input, which can map the d-pad to
        arrow keys and make the page navigable.

        Uses the same browser the user picked for games, so there is one choice
        and one persistent profile — the site and the games share a login.
        """
        browser = await asyncio.to_thread(browsers.resolve_browser, self._settings.get("browser"))
        if browser is None:
            return None

        launcher = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "shelfcloud-launch.py")
        profile = browsers.profile_dir(browser["app_id"])
        # --no-autoplay: our own site has no provider Play button to press.
        options = _shell_join([
            launcher, browser["app_id"], profile, self._gateway(), "--no-autoplay",
        ])
        return {
            "exe": "/usr/bin/python3",
            "options": options,
            "name": "Shelfcloud (website)",
            "browser": browser["label"],
        }


    async def fetch_artwork(self, url: str) -> Dict[str, Any]:
        """Download one artwork image and return it base64-encoded.

        The frontend used to fetch these itself, which cannot work in general:
        that code runs inside Steam's client at https://steamloopback.host, so
        every image is a cross-origin request. Steam's own CDN happens to send
        access-control-allow-origin and succeeded; SteamGridDB sends no CORS
        header, and our own gateway is plain HTTP, which the client blocks as
        mixed content. Both failed silently and left blank tiles.

        Python has neither restriction, so the bytes are fetched here and
        handed over ready to use.
        """
        # https only. http was accepted here when the gateway was a plaintext
        # box on the LAN; it no longer is, and every artwork URL the catalog
        # serves — 18,884 of them across five CDNs — is already https. Plain
        # http would let anything on the path swap the image, and these bytes
        # are written to disk and handed to Steam as a shortcut icon.
        if not isinstance(url, str) or not url.startswith("https://"):
            return {"ok": False, "error": "unsupported url"}

        headers = {"User-Agent": ARTWORK_USER_AGENT}
        # Our own gateway needs the credential; a provider's CDN must never see
        # it. The artwork proxy is behind sign-in like every other endpoint, and
        # this request used to be the one exception — it went out bare, which is
        # the only reason that endpoint could not be gated.
        if url.startswith(self._gateway() + "/"):
            token = self._token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT, context=SSL_CTX) as response:
                content_type = response.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    return {"ok": False, "error": f"not an image ({content_type})"}
                raw = response.read(ARTWORK_MAX_BYTES + 1)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if len(raw) > ARTWORK_MAX_BYTES:
            return {"ok": False, "error": "image too large"}
        return {
            "ok": True,
            "base64": base64.b64encode(raw).decode(),
            "kind": "png" if content_type.endswith("png") else "jpg",
        }

    async def clear_library(self) -> Dict[str, Any]:
        """Empty the chosen library. Tiles are removed by the next sync."""
        try:
            result = await asyncio.to_thread(self._request, "/api/me/library/clear", "POST", {})
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def preview_owned(self) -> Dict[str, Any]:
        """How many owned-on-Steam games could be added in one go."""
        try:
            result = await asyncio.to_thread(self._request, "/api/me/library/owned-preview")
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def add_owned_games(self) -> Dict[str, Any]:
        """Add every game the user owns on Steam that is playable on cloud.

        The alternative is picking a few hundred games one at a time on the
        website, which is the whole point of having this button.
        """
        try:
            result = await asyncio.to_thread(
                self._request, "/api/me/library/add-owned", "POST", {}
            )
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def sync_steam_library(self) -> Dict[str, Any]:
        """Re-read which games the user owns from Steam."""
        try:
            result = await asyncio.to_thread(
                self._request, "/api/me/steam-library/refresh", "POST", {}
            )
            return {"ok": result.get("ok", False), **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def get_desired_library(self) -> Dict[str, Any]:
        """What the Steam library should contain, ready to act on."""
        try:
            result = await asyncio.to_thread(self._request, "/api/me/library")
        except PermissionError:
            return {"ok": False, "error": "not signed in", "signed_in": False}
        except Exception as exc:
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc), "signed_in": True}

        # Validate the envelope before trusting anything inside it.
        if not isinstance(result, dict):
            self._last_error = "gateway returned an unexpected response"
            return {"ok": False, "error": self._last_error, "signed_in": True}

        games = result.get("games")
        if not isinstance(games, list):
            self._last_error = "gateway returned no game list"
            return {"ok": False, "error": self._last_error, "signed_in": True}

        desired = []
        skipped = 0
        for game in games:
            if not isinstance(game, dict) or game.get("missing"):
                skipped += 1
                continue
            try:
                entry = self._to_desired(game)
            except Exception as exc:
                # One unusable row must never cost the user their whole library.
                decky.logger.warning("Skipping a malformed game entry: %s", exc)
                entry = None
            if entry:
                desired.append(entry)
            else:
                skipped += 1

        if skipped:
            decky.logger.info("Skipped %d entries that were not usable", skipped)

        self._last_error = None
        return {
            "ok": True,
            "signed_in": True,
            "desired": desired,
            "skipped": skipped,
            "tracked": self._synced,
        }

    # ------------------------------------------------------------- shelves
    #
    # A shelf is a named library on the gateway; this device points at one. The
    # Deck does not create or delete them — that is a job for a real keyboard —
    # it only chooses which one it shows, which is the part you want to change
    # while holding the machine.

    async def get_shelves(self) -> Dict[str, Any]:
        """The account's shelves, and which one this device is on.

        Both come from the gateway rather than being cached here: the shelf can
        be changed from the website or from another device, and a stale local
        copy would show the wrong library with no way for the user to tell.
        """
        try:
            shelves = await asyncio.to_thread(self._request, "/api/me/shelves")
            devices = await asyncio.to_thread(self._request, "/api/me/devices")
        except PermissionError:
            return {"ok": False, "error": "not signed in", "signed_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not isinstance(shelves, dict) or not isinstance(devices, dict):
            return {"ok": False, "error": "gateway returned an unexpected response"}

        # `or []` is not enough: it rescues null, but a number or a string
        # reaches the loop below and raises — a string iterates one character
        # at a time and an int is not iterable at all.
        shelf_rows = shelves.get("shelves")
        device_rows = devices.get("devices")
        if not isinstance(shelf_rows, list) or not isinstance(device_rows, list):
            return {"ok": False, "error": "gateway returned an unexpected response"}

        # Match on the id this gateway says is us. With no such id there is no
        # device to speak of, and comparing against None would happily match
        # the first row whose id is also unreadable.
        this_id = as_id(devices.get("this_device"))
        mine: Dict[str, Any] = {}
        if this_id is not None:
            mine = next((d for d in device_rows
                         if isinstance(d, dict) and as_id(d.get("id")) == this_id), {})

        name = mine.get("name")
        return {
            "ok": True,
            "shelves": [s for s in map(clean_shelf, shelf_rows) if s is not None],
            "device_id": this_id,
            "shelf_id": as_id(mine.get("shelf_id")),
            "device_name": name if isinstance(name, str) else None,
        }

    async def set_shelf(self, shelf_id: Optional[int]) -> Dict[str, Any]:
        """Point this device at a shelf, or at None for the shared games.

        The library this device should hold changes as a result, so the caller
        syncs immediately afterwards — leaving the old tiles in place until the
        next poll would look like the setting had not taken.
        """
        current = await self.get_shelves()
        if not current.get("ok"):
            return current

        # `is None`, not falsiness: an id of 0 is a device, and treating it as
        # a missing one would tell a signed-in user to sign in again.
        #
        # device_id has been through as_id(), which is what makes it safe to
        # interpolate into the path below. Straight from the JSON it is
        # whatever the response said, and a string there writes its own URL.
        device_id = current.get("device_id")
        if device_id is None:
            # A token issued before shelves existed has no device attached.
            return {"ok": False, "error": "Sign in again to use shelves on this device"}

        # None is a real choice — the shared library — so an unreadable value
        # must not quietly become it. Anything that is not an id, and any id
        # this account does not have, is refused rather than sent on.
        wanted = as_id(shelf_id)
        if shelf_id is not None and wanted is None:
            return {"ok": False, "error": "that is not a shelf"}
        if wanted is not None and wanted not in {s["id"] for s in current["shelves"]}:
            # Deleted from the website while this panel was open.
            return {"ok": False, "error": "That shelf no longer exists"}

        try:
            await asyncio.to_thread(
                self._request, f"/api/me/devices/{device_id}/shelf", "POST",
                {"shelf_id": wanted},
            )
            return {"ok": True, "shelf_id": wanted}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------- shortcut bookkeeping

    async def record_shortcut(self, game_id: int, app_id: int, name: str,
                              options: str, art: str = "", provider: str = "",
                              url: str = "") -> Dict:
        """Remember a tile, and where its game launches if the gateway is down.

        `url` is the offline fallback. It used to be an argument in the Steam
        shortcut, which put a provider deep link in the game's properties for
        anyone to read; it lives here instead, keyed by game id, and the
        launcher looks it up only when the gateway does not answer.

        https only, and checked here rather than trusted: this file is what the
        launcher falls back to, so a bad value written now is a bad URL opened
        later, when the gateway is unreachable and nothing can correct it.
        """
        previous = self._synced.get(str(game_id)) or {}
        keep = url if isinstance(url, str) and url.startswith("https://") else ""
        self._synced[str(game_id)] = {
            "app_id": app_id, "name": name, "launch": options,
            "art": art, "provider": provider,
            # Keep the previous fallback rather than blanking it when a caller
            # omits one — an older frontend that does not send `url` yet must
            # not silently strip the offline route from every tile it touches.
            "url": keep or previous.get("url", ""),
        }
        await self._save_state()
        return {"ok": True}

    async def stray_shortcuts(self) -> Dict:
        """Shelfcloud tiles Steam has that this plugin does not account for.

        Reconcile can only remove what it tracks, so a shortcut created but
        never recorded — a restart between the two, a rolled-back state file —
        is invisible to it forever, and the next sync makes a SECOND tile for
        the same game. That is how "Ad Infinitum" ended up on the shelf twice.

        shortcuts.vdf is the only place that knows the truth, and every tile of
        ours carries `--game-id N` in its launch options, so the file says which
        game each one belongs to.

        Reports rather than repairs: removing a shortcut is SteamClient's job,
        and rewriting this file under a running Steam loses whatever it flushes
        next.
        """
        try:
            found = await asyncio.to_thread(shortcuts.shelfcloud_shortcuts)
        except Exception as exc:
            decky.logger.error("could not read shortcuts.vdf: %s", exc)
            return {"ok": False, "error": str(exc), "shortcuts": []}

        # What this plugin believes, so the caller can prefer the tile that
        # already carries artwork and playtime over an accidental twin.
        tracked: Dict[int, int] = {}
        for game_id, entry in self._synced.items():
            app_id = as_id((entry or {}).get("app_id"))
            key = as_id(game_id)
            if key is not None and app_id is not None:
                tracked[key] = app_id

        by_game: Dict[int, List[int]] = {}
        for entry in found:
            by_game.setdefault(entry["game_id"], []).append(entry["app_id"])

        return {
            "ok": True,
            "shortcuts": [
                {
                    "game_id": game_id,
                    "app_ids": app_ids,
                    # Keep the one already tracked; failing that the oldest,
                    # which is the one likelier to hold playtime.
                    "keep": tracked[game_id] if tracked.get(game_id) in app_ids
                            else app_ids[0],
                    "tracked": tracked.get(game_id),
                }
                for game_id, app_ids in by_game.items()
            ],
        }

    async def forget_shortcut(self, game_id: int) -> Dict:
        self._synced.pop(str(game_id), None)
        # The tile is gone, so its icon file is dead weight. Left alone these
        # accumulate forever: one per game ever added, never read again.
        for suffix in ("png", "jpg"):
            try:
                os.remove(os.path.join(_icon_dir(), f"{int(game_id)}.{suffix}"))
            except (OSError, ValueError):
                pass
        await self._save_state()
        return {"ok": True}

    async def save_icon(self, game_id: int, data: str, kind: str) -> Dict:
        """Write an image to disk and return the path, for SetShortcutIcon.

        Steam takes the four library slots as bytes but takes an icon as a
        filesystem path, so this is the one piece of artwork that has to exist
        as a file. The frontend already holds the bytes — it fetched the logo
        for the logo slot — so they are passed through rather than downloaded
        a second time.
        """
        try:
            game_id = int(game_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad game id"}
        if kind not in ("png", "jpg"):
            return {"ok": False, "error": f"unsupported kind {kind!r}"}
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception as exc:
            return {"ok": False, "error": f"bad image data: {exc}"}
        if not raw or len(raw) > ARTWORK_MAX_BYTES:
            return {"ok": False, "error": "image missing or too large"}

        path = os.path.join(_icon_dir(), f"{game_id}.{kind}")
        try:
            # Written whole then moved, so Steam can never read a file that is
            # half-written — it reads this path whenever it draws the icon,
            # not just once at save time.
            temporary = f"{path}.part"
            with open(temporary, "wb") as handle:
                handle.write(raw)
            os.replace(temporary, path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}

    async def sync_finished(self, added: int, removed: int, updated: int) -> Dict:
        self._last_sync = asyncio.get_event_loop().time()
        decky.logger.info("Sync: +%d -%d ~%d (tracked=%d)",
                          added, removed, updated, len(self._synced))
        return {"ok": True}

    # ---------------------------------------------------------------- status

    def _provider_counts(self) -> Dict[str, int]:
        """How many synced tiles belong to each provider, for the panel chips.

        Tiles recorded before providers were tracked have no provider. They are
        counted as unknown rather than assumed to be Boosteroid: that guess was
        harmless while Boosteroid was the only provider and is simply wrong now
        that a tile could equally be Xbox Cloud. The next sync fills it in.
        """
        counts: Dict[str, int] = {}
        for entry in self._synced.values():
            provider = entry.get("provider") or "unknown"
            counts[provider] = counts.get(provider, 0) + 1
        return counts

    def _sync_minutes(self) -> int:
        """Minutes between automatic syncs, clamped to a sane range."""
        try:
            minutes = int(self._settings.get("sync_minutes", DEFAULT_SYNC_MINUTES))
        except (TypeError, ValueError):
            return DEFAULT_SYNC_MINUTES
        return max(1, min(minutes, 24 * 60))

    async def _verify_auth(self) -> bool:
        """Is the stored token still accepted? Re-checked at most once a minute.

        Without this the panel trusted a string on disk. A revoked or expired
        token then looked like a working session: every button was enabled and
        every one of them failed.
        """
        if not self._token():
            return False
        now = time.monotonic()
        if now - self._auth_checked < AUTH_RECHECK_SECONDS:
            return self._auth_ok
        self._auth_checked = now
        try:
            await asyncio.to_thread(self._request, "/api/me")
            self._auth_ok = True
        except PermissionError:
            self._auth_ok = False      # _request already cleared the token
        except Exception:
            # Gateway unreachable is not "signed out" — do not throw away a
            # perfectly good credential because the network blipped.
            pass
        return self._auth_ok

    def _version(self) -> str:
        """The installed plugin's version, read from package.json.

        Read from disk rather than held as a constant here so there is one
        source of truth: Decky itself shows the version from package.json, and
        a second copy in Python would eventually disagree with it — an About
        box that confidently states the wrong version is worse than none.

        Cached after the first read: the file cannot change under a running
        plugin, since installing a new one restarts the loader.
        """
        if self._version_cache is None:
            self._version_cache = "unknown"
            try:
                manifest = os.path.join(decky.DECKY_PLUGIN_DIR, "package.json")
                with open(manifest, encoding="utf-8") as handle:
                    value = json.load(handle).get("version")
                if isinstance(value, str) and value.strip():
                    self._version_cache = value.strip()
            except Exception as exc:
                decky.logger.warning("Could not read plugin version: %s", exc)
        return self._version_cache

    async def get_status(self) -> Dict[str, Any]:
        browser = await asyncio.to_thread(browsers.resolve_browser, self._settings.get("browser"))
        signed_in = await self._verify_auth()
        return {
            "signed_in": signed_in,
            "version": self._version(),
            "steam_id64": self._settings.get("steam_id64"),
            "tracked": len(self._synced),
            "by_provider": self._provider_counts(),
            "browser": browser,
            "browsers": await asyncio.to_thread(browsers.detect_browsers),
            "installable": await asyncio.to_thread(browsers.installable_browsers),
            "install": dict(self._install),
            "sync_minutes": self._sync_minutes(),
            "sync_choices": SYNC_INTERVAL_CHOICES,
            "last_error": self._last_error,
            # Only while a sign-in is in flight; the browser that
            # asks for this closed the panel that first showed it.
            "pending_login_code": (
                self._pending_login_code
                if self._login_task is not None and not self._login_task.done()
                else ""
            ),
        }

    # ------------------------------------------------- installing a browser

    async def install_browser(self, app_id: str) -> Dict[str, Any]:
        """Install one of the offered browsers, in the background.

        Returns immediately. Progress is read from get_status(), so the panel
        can be closed and reopened mid-download without losing it — the same
        reason the sync reports that way.

        Refuses anything not on the offer list. `app_id` arrives from the
        frontend and ends up in an argv, and while the command is never run
        through a shell, "the caller is our own UI" is not a thing worth
        relying on when the check is one line.
        """
        if not browsers.is_installable(app_id):
            return {"ok": False, "error": "That browser is not one Shelfcloud installs."}
        if self._install.get("running"):
            return {"ok": False, "error": "An install is already running."}

        label = next((b["label"] for b in browsers.INSTALLABLE
                      if b["app_id"] == app_id), app_id)
        self._install = {
            "running": True, "app_id": app_id, "label": label,
            "message": f"Starting the {label} download…", "error": None, "done": False,
        }
        self._install_task = asyncio.create_task(self._run_install(app_id, label))
        return {"ok": True}

    @staticmethod
    def _clean_env() -> dict:
        """A subprocess environment free of Decky's bundled libraries.

        Decky's embedded Python ships its own libssl/libcrypto and sets
        LD_LIBRARY_PATH to find them. That works for Python, but poisons
        any subprocess that links the system OpenSSL — flatpak in particular
        needs the system's version and crashes with "OPENSSL_3.5.0 not found"
        when it picks up Decky's older copy instead.
        """
        env = os.environ.copy()
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("LD_PRELOAD", None)
        return env

    async def _apply_override(self, app_id: str, env: Optional[dict] = None) -> bool:
        """Grant one browser the flatpak permissions cloud gaming needs."""
        if env is None:
            env = self._clean_env()
        try:
            result = await asyncio.to_thread(
                subprocess.run, browsers.override_command(app_id),
                capture_output=True, text=True, timeout=30, env=env,
            )
            if result.returncode == 0:
                decky.logger.info("Applied flatpak override for %s", app_id)
                return True
            decky.logger.warning("Override failed for %s: %s", app_id, result.stderr)
        except Exception as exc:
            decky.logger.warning("Override failed for %s: %s", app_id, exc)
        return False

    async def apply_overrides(self) -> Dict[str, Any]:
        """Apply flatpak overrides to the selected browser."""
        browser = await asyncio.to_thread(
            browsers.resolve_browser, self._settings.get("browser"))
        if not browser:
            return {"ok": False, "error": "no browser selected"}
        ok = await self._apply_override(browser["app_id"])
        return {"ok": ok, "browser": browser["label"]}

    async def _run_install(self, app_id: str, label: str) -> None:
        """Do the install and record how it went. Never raises."""
        clean_env = self._clean_env()
        try:
            await asyncio.to_thread(
                subprocess.run, browsers.ensure_flathub_command(),
                capture_output=True, timeout=60, env=clean_env,
            )

            self._install["message"] = f"Downloading {label}…"
            result = await asyncio.to_thread(
                subprocess.run, browsers.install_command(app_id),
                capture_output=True, text=True, timeout=browsers.INSTALL_TIMEOUT,
                env=clean_env,
            )

            if result.returncode != 0:
                # flatpak puts the useful line last; the rest is progress noise.
                detail = (result.stderr or result.stdout or "").strip().splitlines()
                self._install.update({
                    "error": detail[-1][:200] if detail else
                             f"flatpak exited with code {result.returncode}",
                    "message": f"Could not install {label}.",
                })
                decky.logger.error("Browser install failed: %s", result.stderr)
            elif not await asyncio.to_thread(self._is_present, app_id):
                # Exit code 0 but nothing on disk. Believe the disk.
                self._install.update({
                    "error": "The install reported success but the browser is not there.",
                    "message": f"Could not install {label}.",
                })
            else:
                await self._apply_override(app_id, clean_env)
                self._install["message"] = f"{label} is installed."
                decky.logger.info("Installed %s", app_id)
        except subprocess.TimeoutExpired:
            self._install.update({
                "error": "The download took too long and was stopped.",
                "message": f"Could not install {label}.",
            })
        except Exception as exc:                       # never kill the plugin
            self._install.update({"error": str(exc)[:200],
                                  "message": f"Could not install {label}."})
            decky.logger.exception("Browser install crashed")
        finally:
            self._install["running"] = False
            self._install["done"] = True

    @staticmethod
    def _is_present(app_id: str) -> bool:
        return any(b["app_id"] == app_id for b in browsers.detect_browsers())

    async def dismiss_install(self) -> Dict[str, Any]:
        """Clear a finished install's message, so the panel can move on."""
        if not self._install.get("running"):
            self._install = dict(IDLE_INSTALL)
        return await self.get_status()

    # Keys the frontend may write. Anything else — especially api_token — is
    # rejected. Shelf selection goes through set_shelf, not here.
    # If a new dropdown is added to the panel, its key must be listed here.
    ALLOWED_SETTINGS = {"browser", "sync_minutes"}

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key not in self.ALLOWED_SETTINGS:
            decky.logger.warning("Rejected setting key: %s", key)
            return await self.get_status()
        self._settings[key] = value
        await self._save_settings()
        return await self.get_status()

    # ------------------------------------------------------------ poll loop

    async def _poll_loop(self):
        """Ask the gateway for changes periodically and nudge the frontend.

        Python cannot create shortcuts — only the frontend can reach
        SteamClient — so this emits an event rather than acting directly.
        """
        # Let Steam settle, then a random extra wait so a fleet that updated
        # together does not poll together. Without it, every copy of the plugin
        # rolled out in one release starts within seconds of the others and
        # stays in lockstep for as long as it runs.
        base = max(MIN_SYNC_SECONDS, self._sync_minutes() * 60)
        spread = random.uniform(0, min(base, STARTUP_SPREAD_MAX_SECONDS))
        await asyncio.sleep(STARTUP_SETTLE_SECONDS + spread)

        while True:
            try:
                if self._token():
                    await decky.emit(SYNC_EVENT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                decky.logger.warning("Poll failed: %s", exc)

            # Read the interval each tick rather than capturing it once, so
            # changing the setting takes effect on the next tick instead of
            # needing a plugin reload.
            await asyncio.sleep(next_poll_delay(
                max(MIN_SYNC_SECONDS, self._sync_minutes() * 60)
            ))
