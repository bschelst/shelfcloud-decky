# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
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
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import decky

import browsers
import display
import weburl
import shortcuts
from poll import (  # re-exported so tests that import main.X keep working
    SYNC_INTERVAL_CHOICES, DEFAULT_SYNC_MINUTES, MIN_SYNC_SECONDS,
    SYNC_JITTER, STARTUP_SETTLE_SECONDS, STARTUP_SPREAD_MAX_SECONDS,
    next_poll_delay,
)
from plugin_helpers import shell_join as _shell_join, hardware_label as _hardware_label

from helpers import get_ssl_context  # provided by Decky Loader (certifi-backed)

SSL_CTX = get_ssl_context()


DEFAULT_GATEWAY = "https://shelfcloud.cc"

# No install has been started. Also the shape the frontend reads, so it can
# rely on the keys existing rather than guarding each one.
IDLE_INSTALL = {
    "running": False, "app_id": None, "label": None,
    "message": "", "error": None, "done": False,
}
REQUEST_TIMEOUT = 20
SYNC_EVENT = "shelfcloud/library"

# Custom layout VDFs, by name: {name: (fetched_at, text)}. See
# _download_custom_layout for why this exists.
_LAYOUT_CACHE: Dict[str, Any] = {}
_LAYOUT_CACHE_TTL = 600

# Must match backend/providers.py LAYOUT_GAMEPAD and LAYOUT_WEB_BROWSER.
_LAYOUT_GAMEPAD = "template://controller_neptune_gamepad_fps.vdf"
_LAYOUT_WEB_BROWSER = "template://controller_neptune_webbrowser.vdf"


def _register_configset(config_dir: str, app_key: str) -> None:
    """Register a per-game config in the configset file so Steam finds it.

    Without this entry Steam ignores the VDF file in the per-game directory.
    boosteroid-steamos does the same thing in _update_configset.
    """
    configset = os.path.join(config_dir, "configset_controller_neptune.vdf")
    if not os.path.isfile(configset):
        return
    with open(configset, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    entry = f'"{app_key}"'
    if entry in text:
        return
    # Insert before the final closing brace
    insertion = f'\t{entry}\n\t{{\n\t\t"autosave" "1"\n\t}}\n'
    idx = text.rfind("}")
    if idx >= 0:
        text = text[:idx] + insertion + text[idx:]
        with open(configset, "w", encoding="utf-8") as f:
            f.write(text)


def _provider_default_layout(provider_key: str) -> Optional[str]:
    """Fallback when the catalog row carries no per-game controller_layout."""
    if provider_key in weburl.WEBSITE_PROVIDERS:
        return _LAYOUT_WEB_BROWSER
    return _LAYOUT_GAMEPAD

# Artwork is fetched here rather than in the browser; see fetch_artwork.
#
# The hardware goes in the comment field so the gateway can name this device
# something a person recognises in a list.
ARTWORK_USER_AGENT = (
    f"Shelfcloud/0.1 ({_hardware_label()}; +https://github.com/bschelst/shelfcloud)")
ARTWORK_MAX_BYTES = 8 * 1024 * 1024

# The most any gateway reply may be. Generous on purpose — the biggest is the
# whole catalogue, a few megabytes — but not unbounded: a captive portal or a
# broken proxy answering a stream of bytes should fail, not fill the Deck's
# memory with it.
RESPONSE_MAX_BYTES = 64 * 1024 * 1024

# A controller layout is a few kilobytes of VDF text. The largest this plugin
# ships is under 20 KB, so a megabyte is already far past anything genuine —
# and unlike the catalogue, this one is held in a module-level cache.
LAYOUT_MAX_BYTES = 1024 * 1024

# How many library entries one change may carry. The gateway refuses more than
# 500, and a cancelled bulk add is routinely larger than that, so the caller
# splits rather than losing the tail to a 422.
_LIBRARY_BATCH = 200

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


# How long the panel may reuse the last display probe. Docking takes seconds;
# the panel asks every 1.5 of them.
DISPLAY_CACHE_SECONDS = 15.0


def _tracking_key(game_id: int, provider: str) -> str:
    """How a tile is identified in synced.json.

    It used to be the game id alone, which was enough while a game could only
    have one tile. With sync_all_providers on it no longer is: Fortnite gets a
    tile per service, they all carry game id 42, and a map keyed by game id
    holds exactly one of them — so the second tile overwrites the first in the
    record and the first becomes an orphan nothing will ever clean up.

    Composite whether or not the setting is on. A key that changes shape when a
    toggle flips is a key that orphans every tile the moment it is flipped, and
    the migration below only has to run once this way.
    """
    return f"{game_id}:{provider}"


def _key_game_id(key: str) -> Optional[int]:
    """The game id back out of a tracking key, for old keys too."""
    try:
        return int(str(key).split(":", 1)[0])
    except (TypeError, ValueError):
        return None


def _settings_icon_dir() -> str:
    """The fallback home for icons, inside the plugin's settings directory."""
    path = os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "icons")
    os.makedirs(path, exist_ok=True)
    return path


def _steam_grid_dir() -> Optional[str]:
    """Steam's own grid folder for the account that is signed in, if found.

    Picked by the newest shortcuts.vdf: a Deck can hold several accounts and
    the one being written to is the one in use.
    """
    files = shortcuts.shortcuts_files()
    if not files:
        return None
    try:
        newest = max(files, key=os.path.getmtime)
        grid = os.path.join(os.path.dirname(newest), "grid")
        os.makedirs(grid, exist_ok=True)
        return grid
    except OSError:
        return None


def _icon_name(game_id: int, app_id: Optional[int], kind: str) -> str:
    """The filename for one tile's icon.

    `<app_id>_icon.<kind>` whenever the shortcut's app id is known, which is
    the shape every other tool on a Deck writes into Steam's grid folder. The
    game-id form is the legacy name and only survives so icons written by an
    older version can still be found and removed.
    """
    if app_id:
        return f"{int(app_id)}_icon.{kind}"
    return f"{int(game_id)}.{kind}"


def _icon_dir() -> str:
    """Where shortcut icons live on disk.

    Icons are not like the other artwork. The four library slots are handed to
    Steam as bytes and Steam stores its own copy; an icon is stored in
    shortcuts.vdf as a PATH, and Steam reads that file every time it draws the
    icon. Delete the file and the icon breaks — so this must be somewhere
    durable, NOT the plugin directory, which install-to-deck.sh overwrites.

    Steam's own grid folder first. The settings directory is durable and was
    the original choice for exactly that reason, but a durable file Steam will
    not draw is no better than no file: an icon written there landed correctly
    in shortcuts.vdf, as a valid PNG the `deck` user could read, and still
    rendered as a broken image. Every other working shortcut on the same
    device — sixteen of them — keeps its icon inside
    `userdata/<id>/config/grid`, and ours was the only one that did not.
    Steam's UI is a browser process, and a path outside its own tree is the
    difference that is actually there.

    DECKY_PLUGIN_SETTINGS_DIR stays as the fallback for the case where Steam's
    directory cannot be found at all, which is better than refusing to write
    an icon.
    """
    return _steam_grid_dir() or _settings_icon_dir()


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
        # Tracked tiles that shortcuts.vdf did not mention LAST time it was
        # read. Forgetting one takes two consecutive misses — see
        # _forget_vanished_tiles for why one is not enough.
        self._vanish_suspects: set = set()
        # (when, answer) for the display probe, which shells out to xrandr and
        # is asked for by a panel that polls status every 1.5 seconds.
        self._display_cache: tuple = (0.0, "")
        self._poll_task: Optional[asyncio.Task] = None
        self._login_task: Optional[asyncio.Task] = None
        # The pairing code for a sign-in in progress, shown in the panel
        # so the person can read it while the browser asks for it.
        self._pending_login_code: str = ""
        self._pending_login_url: str = ""
        self._last_error: Optional[str] = None
        self._last_sync: Optional[float] = None
        # Whether the gateway last accepted our token. A token can stop working
        # without the plugin doing anything, so "we have a string on disk" is
        # not the same as "we are signed in".
        self._auth_ok: bool = True
        self._auth_checked: float = 0.0
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
        # Our own icons, BY NAME. This used to empty the whole directory, which
        # was safe only while that directory belonged to this plugin alone.
        # It is now Steam's grid folder, which holds the library artwork of
        # every game on the machine — emptying it on uninstall would delete
        # all of it.
        # The game id is the KEY here, not a field — a tracked entry holds
        # app_id, name, launch, art, provider and url, and nothing else. Read
        # as `tracked.get("game_id")` it is always None, which silently skips
        # the legacy filename half of the cleanup below.
        for key, tracked in self._synced.items():
            # The key is "<game id>:<provider>" now, so the game id has to be
            # taken back out of it — passed whole it matches no icon filename
            # and the legacy half of this cleanup silently does nothing.
            self._remove_icon_files(_key_game_id(key), tracked.get("app_id"))

        # The old home, which really was ours alone, so it can still go wholly.
        legacy = _settings_icon_dir()
        if os.path.isdir(legacy):
            for name in os.listdir(legacy):
                try:
                    os.remove(os.path.join(legacy, name))
                except OSError:
                    pass
            try:
                os.rmdir(legacy)
            except OSError:
                pass
        decky.logger.info("Shelfcloud uninstalled")

    @staticmethod
    def _remove_icon_files(game_id: Any, app_id: Any) -> None:
        """Delete one tile's icon wherever a version of this plugin put it.

        Both directories and both naming schemes, because an icon written by
        an earlier version is still on disk under the old name and would
        otherwise be left behind forever.
        """
        for directory in {_settings_icon_dir(), _icon_dir()}:
            for suffix in ("png", "jpg"):
                for name in filter(None, (
                    _icon_name(game_id, app_id, suffix) if app_id else None,
                    f"{game_id}.{suffix}" if game_id is not None else None,
                )):
                    try:
                        os.remove(os.path.join(directory, name))
                    except (OSError, ValueError, TypeError):
                        pass

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
            return cls._migrate_keys(state)

        recovered = cls._recover_from_steam()
        if recovered:
            decky.logger.warning(
                "state file missing or unreadable; recovered %d tile(s) from "
                "shortcuts.vdf rather than recreating them", len(recovered))
        return recovered

    @staticmethod
    def _migrate_keys(state: Dict) -> Dict:
        """Re-key an old synced.json from "42" to "42:boosteroid".

        One-way and idempotent: a key that already holds a colon is left alone,
        so this can run on every load and only does work once.

        An entry with no provider recorded keeps its bare key. It cannot be
        given the right one by guessing, and a wrong provider in the key is
        worse than an old-shaped one — the tile would stop matching anything
        desired and be deleted as no longer wanted, losing its playtime and its
        artwork. Left as it is, it is picked up by the compatibility lookup in
        the frontend and re-keyed the next time it is written.
        """
        if not isinstance(state, dict):
            return {}

        migrated: Dict = {}
        moved = 0
        for key, value in state.items():
            if not isinstance(value, dict) or ":" in str(key):
                migrated[key] = value
                continue
            provider = value.get("provider")
            if not isinstance(provider, str) or not provider:
                migrated[key] = value
                continue
            game_id = _key_game_id(key)
            if game_id is None:
                migrated[key] = value
                continue
            migrated[_tracking_key(game_id, provider)] = value
            moved += 1

        if moved:
            decky.logger.info("re-keyed %d tracked tile(s) by game and provider", moved)
        return migrated

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
            provider = entry.get("provider") or ""
            # Keyed the same way a live sync keys it, so a recovered tile is
            # matched rather than recreated beside itself. A tile from a build
            # that wrote no --provider keeps the bare game id: the frontend's
            # compatibility lookup still finds it, and the next write gives it
            # the current shape.
            key = _tracking_key(entry["game_id"], provider) if provider \
                else str(entry["game_id"])
            # setdefault: duplicates() documents that Steam's order is oldest
            # first, and the oldest tile is the one holding the playtime and
            # the controller layout.
            found_entry = {"app_id": entry["app_id"]}
            if provider:
                found_entry["provider"] = provider
            recovered.setdefault(key, found_entry)
        return recovered

    @staticmethod
    def _read_json(path: str) -> Dict:
        """Read a JSON object from disk, or {} for anything else at all.

        Two things this used to get wrong, both of which take the whole plugin
        down rather than the one file.

        It caught FileNotFoundError and JSONDecodeError only. A settings file
        with a wrong mode raises PermissionError; one with a corrupted byte
        raises UnicodeDecodeError, which is NOT a JSONDecodeError; a path that
        is somehow a directory raises IsADirectoryError. Any of those reached
        _main and stopped the plugin loading at all.

        And it returned whatever JSON happened to parse to. `[]` in
        settings.json is valid JSON and not a mapping, so `self._settings`
        became a list and every `.get()` after it raised AttributeError — a
        plugin that loads and then fails at everything, which is harder to
        diagnose than one that does not load.

        Returning {} is right for both: an unreadable settings file means "no
        settings", and the caller that cares about losing state — _load_state
        — already treats {} as "ask Steam instead" rather than "no tiles".
        """
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as exc:
            # ValueError covers JSONDecodeError and UnicodeDecodeError; OSError
            # covers missing, unreadable, and not-a-file.
            if not isinstance(exc, FileNotFoundError):
                decky.logger.warning("could not read %s: %s", path, exc)
            return {}
        if not isinstance(loaded, dict):
            decky.logger.warning("%s holds %s, not an object; ignoring it",
                                 path, type(loaded).__name__)
            return {}
        return loaded

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
                # Bounded, like fetch_artwork already is. An unbounded read
                # here trusts whatever answered to be reasonable about size,
                # and the largest thing this asks for — a whole catalogue — is
                # a few megabytes. Reading one byte past the cap is what tells
                # the difference between "at the limit" and "over it".
                raw = response.read(RESPONSE_MAX_BYTES + 1)
                if len(raw) > RESPONSE_MAX_BYTES:
                    raise ValueError(
                        f"gateway sent more than {RESPONSE_MAX_BYTES} bytes")
                payload = raw.decode()
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
        self._pending_login_code = ""
        self._pending_login_url = ""
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
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
            self._pending_login_url = result.get("login_url") or ""

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
            # Not a library slot. Steam takes an icon as a file PATH, so this
            # one is downloaded and written to disk rather than handed over as
            # bytes — see applyArtwork. It is separate from the logo because
            # the logo is drawn INSTEAD OF the title on a game's page: a tile
            # that carries one shows a picture where its name should be, which
            # is why website tiles have no logo and do have this.
            "icon": url("icon_url"),
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
                for slot in ("capsule", "header", "hero", "logo", "icon")}

    def _to_desired(self, game: Dict) -> List[Dict]:
        """Turn a library entry into the tiles the frontend should make.

        A list, because one game is not always one tile. With
        sync_all_providers on, a game playable on three services becomes three
        tiles — one per service, each launching that service — so the library
        shows where a game can actually be played rather than only where this
        device would have picked. Off, which is the default, it is the single
        preferred route and the list has one entry.

        Every field is checked rather than indexed. This reads data from a
        network service, and one malformed entry used to raise straight out of
        the loop and abandon the whole sync — so a single odd row in the
        catalog could stop every other game from appearing. Anything that does
        not have what a Steam shortcut needs is skipped and counted.
        """
        if not isinstance(game, dict):
            return []

        game_id = game.get("id")
        name = game.get("name")
        if not isinstance(game_id, int) or not isinstance(name, str) or not name.strip():
            return []

        raw_providers = game.get("providers")
        providers = [p for p in raw_providers if isinstance(p, dict)] \
            if isinstance(raw_providers, list) else []
        # A route is only usable with somewhere to go and something to call it.
        #
        # Two rules, because there are two kinds of tile. A game is https-only:
        # its URL is written into a Steam shortcut and opened later in a window
        # holding live provider sessions, and refusing http here keeps a plain
        # link from ever being stored. A page is checked by weburl instead,
        # which permits http but refuses anything inside this network — the same
        # rule the launcher applies when it opens the thing, so an entry cannot
        # become a shortcut that then refuses to launch.
        providers = [
            p for p in providers
            if isinstance(p.get("provider"), str) and p["provider"]
            and isinstance(p.get("launch_url"), str)
            and (weburl.is_allowed_website_url(p["launch_url"])
                 if p["provider"] in weburl.WEBSITE_PROVIDERS
                 else p["launch_url"].startswith("https://"))
        ]
        if not providers:
            return []

        chosen = game.get("chosen_provider")
        if self._sync_all_providers():
            # Every route, preferred one first so it keeps whichever tile Steam
            # happens to order first and the library reads the way it did.
            ordered = sorted(providers, key=lambda p: p["provider"] != chosen)
            # One route per SERVICE, not one per row.
            #
            # The catalog can hold several rows for the same game on the same
            # service — a regional entry, a re-release, a second storefront.
            # Measured on this account: 402 boosteroid rows across 316 games,
            # and games listing six, seven and eight "providers" when only
            # five exist. One tile per row gives them all the same key, so
            # they collide in synced.json: the sync makes a tile, makes it
            # again for the next row, and the launch URL flips between them.
            routes = []
            taken = set()
            for route in ordered:
                if route["provider"] in taken:
                    continue
                taken.add(route["provider"])
                routes.append(route)
        else:
            routes = [next((p for p in providers if p["provider"] == chosen),
                           providers[0])]

        browser = browsers.resolve_browser(self._settings.get("browser"))
        if browser is None:
            return []

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
        # --provider stays, and now carries a second job: what a signed-out
        # page looks like and where the Play control lives differ per provider,
        # and with one tile per service it is also what tells the gateway which
        # of them this tile is. Three tiles for one game share a game id, so
        # without it all three resolve to the same service and two of them lie
        # about what they open.
        artwork = self._artwork(game)
        artwork_proxy = self._artwork_proxy(game)

        entries: List[Dict] = []
        for provider in routes:
            key = provider["provider"]
            options = _shell_join([
                launcher, browser["app_id"], profile,
                "--game-id", str(game_id),
                "--provider", key,
            ])
            entries.append({
                "key": _tracking_key(game_id, key),
                "game_id": game_id,
                "url": provider["launch_url"],
                "name": name,
                "provider": key,
                "exe": "/usr/bin/python3",
                "options": options,
                "artwork": artwork,
                "artwork_proxy": artwork_proxy,
                "controller_layout": (provider.get("controller_layout")
                                      or _provider_default_layout(key)),
            })
        return entries

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

        # On a thread. urlopen BLOCKS, and this is an async method on the one
        # event loop the whole plugin shares — so a slow CDN did not just slow
        # this image down, it stopped the backend: the poll loop, every other
        # RPC, the sync's own progress. With a 20 second timeout, several
        # artwork slots per game and hundreds of games, a single unreachable
        # host could hold the plugin still for minutes.
        try:
            content_type, raw = await asyncio.to_thread(
                self._fetch_image, url, headers)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not content_type.startswith("image/"):
            return {"ok": False, "error": f"not an image ({content_type})"}
        if len(raw) > ARTWORK_MAX_BYTES:
            return {"ok": False, "error": "image too large"}
        return {
            "ok": True,
            "base64": base64.b64encode(raw).decode(),
            "kind": "png" if content_type.endswith("png") else "jpg",
        }

    @staticmethod
    def _fetch_image(url: str, headers: Dict[str, str]) -> tuple:
        """Blocking half of fetch_artwork, for asyncio.to_thread.

        Reads one byte more than the limit so the caller can tell "exactly at
        the limit" from "too big" without holding the whole thing in memory.
        """
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT,
                                    context=SSL_CTX) as response:
            content_type = response.headers.get("Content-Type", "")
            return content_type, response.read(ARTWORK_MAX_BYTES + 1)

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
        """Add every game the user can play on cloud.

        No provider argument, so the gateway answers for all of them at once:
        games owned on Steam, free-to-play ones, and — since a subscription is
        itself the entitlement — whatever the user's Luna or Xbox Cloud tier
        already covers.

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

    async def drop_from_library(self, game_ids: List[int]) -> Dict[str, Any]:
        """Take games back out of the library, for a cancelled bulk add.

        "Add all my games" adds every owned game server-side in ONE call and
        then makes the tiles one at a time. Cancelling stops the tiles, which
        left the library holding hundreds of games that would simply appear on
        the next sync — so the cancel undid nothing that lasted.

        Removing them makes cancel mean what it says. Only games this run added
        and never tiled are passed in; anything already on the shelf is the
        person's own choice and is not touched.

        Sent in batches because the endpoint caps a change at 500 entries, and
        a bulk add is routinely larger than that.
        """
        # The ARGUMENT before its contents. This list is built by the frontend
        # out of a gateway response, so it is exactly as trustworthy as that
        # response was — and `for g in None` is a TypeError out of a Decky
        # callable, which surfaces as a dead panel with nothing to explain it.
        if not isinstance(game_ids, (list, tuple, set)):
            return {"ok": True, "removed": 0}

        # as_id, not a hand-rolled check: JSON promises nothing about types, so
        # an id arrives as an int here, a float from a language whose numbers
        # are all floats, and a string from a proxy that rewrote the body. That
        # conversion already exists and already rejects bool, which is a
        # subclass of int and would otherwise become the id 1 — a real game.
        ids = [value for value in (as_id(g) for g in game_ids) if value is not None]
        if not ids:
            return {"ok": True, "removed": 0}

        removed = 0
        try:
            for start in range(0, len(ids), _LIBRARY_BATCH):
                batch = ids[start:start + _LIBRARY_BATCH]
                result = await asyncio.to_thread(
                    self._request, "/api/me/library", "POST", {"remove": batch})
                removed += int(result.get("removed") or 0)
        except Exception as exc:
            # Best effort. The tiles are already correct; this is tidying the
            # library behind them, and failing it must not fail the sync.
            decky.logger.warning("Shelfcloud: could not drop %d games: %s",
                                 len(ids), exc)
            return {"ok": False, "error": str(exc), "removed": removed}
        return {"ok": True, "removed": removed}

    async def restore_to_library(self, game_ids: List[int]) -> Dict[str, Any]:
        """Put games back, for a cancelled "remove all".

        That run empties the library server-side in one call and then deletes
        the tiles one at a time. Stopping the deletions alone would change
        nothing that lasts: the library is already empty, so every tile the
        cancel saved is no longer wanted and the next sync removes it anyway.

        So the games whose tiles survived are added back. Only those — a game
        whose tile was already deleted is gone, and putting it back would
        undo a deletion the person watched happen.
        """
        if not isinstance(game_ids, (list, tuple, set)):
            return {"ok": True, "restored": 0}
        ids = [value for value in (as_id(g) for g in game_ids) if value is not None]
        if not ids:
            return {"ok": True, "restored": 0}

        restored = 0
        try:
            for start in range(0, len(ids), _LIBRARY_BATCH):
                batch = [{"game_id": game_id} for game_id in ids[start:start + _LIBRARY_BATCH]]
                result = await asyncio.to_thread(
                    self._request, "/api/me/library", "POST", {"add": batch})
                restored += int((result or {}).get("added") or 0)
        except Exception as exc:
            decky.logger.warning("Shelfcloud: could not restore %d games: %s",
                                 len(ids), exc)
            return {"ok": False, "error": str(exc), "restored": restored}
        return {"ok": True, "restored": restored}

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
                entries = self._to_desired(game)
            except Exception as exc:
                # One unusable row must never cost the user their whole library.
                decky.logger.warning("Skipping a malformed game entry: %s", exc)
                entries = []
            if entries:
                desired.extend(entries)
            else:
                skipped += 1

        if skipped:
            decky.logger.info("Skipped %d entries that were not usable", skipped)

        await self._forget_vanished_tiles()

        self._last_error = None
        return {
            "ok": True,
            "signed_in": True,
            "desired": desired,
            "skipped": skipped,
            "tracked": self._synced,
        }

    async def _forget_vanished_tiles(self) -> int:
        """Drop tracked tiles whose Steam shortcut is gone.

        stray_shortcuts covers one direction — Steam has a tile we never wrote
        down — and nothing covered the other. A tile removed from shortcuts.vdf
        behind the plugin's back stays in synced.json forever, and every sync
        then treats it as present: it is "existing", so no new tile is made,
        and the reconcile updates a shortcut that is not there. The game simply
        stops being in the library, and no amount of syncing brings it back.

        It also cannot be filed into its collection, because appStore has never
        heard of the app id — which is why "Decimated (Boosteroid)" left the
        Boosteroid collection one short of its tracked count, permanently, for
        as long as anyone had been looking at it.

        Forgetting the entry is the whole fix: the next sync sees the game as
        untracked and makes it a fresh tile.

        Reads shortcuts.vdf rather than trusting appStore. A tile Steam simply
        has not indexed yet is still IN the file, so only a genuinely absent
        one is dropped — appStore alone would throw away tiles that were merely
        new.

        TWO STRIKES, because that file is written when Steam feels like it.
        Measured here: the last sync ran at 01:07 and shortcuts.vdf still had
        its 01:30 timestamp from a game launch twenty-three minutes later — so
        a tile created moments ago can be genuinely missing from the copy on
        disk. Forgetting it there would make the next sync build a SECOND tile
        for the same game, which is the duplicate-tile disaster _load_state
        exists to prevent, arrived at from the other direction.

        A tile has to be absent on two consecutive passes, minutes apart. A
        flush that has not happened yet survives one pass; a shortcut somebody
        actually deleted survives none. The suspicion is in memory only: after
        a restart everything gets the benefit of the doubt again, which is the
        right way round for something whose mistake is a duplicate.
        """
        if not self._synced:
            return 0
        try:
            found = await asyncio.to_thread(shortcuts.shelfcloud_shortcuts)
        except Exception as exc:                      # never fail a sync over it
            decky.logger.warning("could not read shortcuts.vdf: %s", exc)
            return 0
        # An empty read means the file could not be parsed, not that Steam has
        # no shortcuts. Forgetting everything on that would recreate the whole
        # library under new app ids, losing every playtime and layout with it.
        if not found:
            return 0

        real = {entry["app_id"] for entry in found}
        missing = {key for key, entry in self._synced.items()
                   if isinstance(entry, dict) and entry.get("app_id") not in real}

        # Only the ones missing last time too. Everything else is put on the
        # list and left alone, and anything that came back drops off it.
        gone = sorted(missing & self._vanish_suspects)
        self._vanish_suspects = missing - set(gone)
        if not gone:
            return 0

        for key in gone:
            decky.logger.info("tile gone from Steam on two passes, forgetting "
                              "%s (%s)", key, (self._synced[key] or {}).get("name", "?"))
            self._synced.pop(key, None)
        await self._save_state()
        return len(gone)

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

    def _resolve_key(self, key: Any, provider: str = "") -> str:
        """The tracking key to write, accepting either shape.

        A frontend from before the per-provider split sends a bare game id.
        Rather than trust whatever arrives, it is normalised here: with a
        provider to hand the composite key is built, and the old-shaped entry
        it replaces is dropped so the tile is not recorded twice under two
        different keys.
        """
        text = str(key)
        if ":" in text:
            return text
        game_id = _key_game_id(text)
        if game_id is None or not provider:
            return text
        composite = _tracking_key(game_id, provider)
        # The same tile under its old key, now superseded.
        if text in self._synced and text != composite:
            self._synced.pop(text, None)
        return composite

    async def record_shortcut(self, game_id: Any, app_id: int, name: str,
                              options: str, art: str = "", provider: str = "",
                              url: str = "") -> Dict:
        """Remember a tile, and where its game launches if the gateway is down.

        `url` is the offline fallback. It used to be an argument in the Steam
        shortcut, which put a provider deep link in the game's properties for
        anyone to read; it lives here instead, keyed by game and provider, and
        the launcher looks it up only when the gateway does not answer.

        https only, and checked here rather than trusted: this file is what the
        launcher falls back to, so a bad value written now is a bad URL opened
        later, when the gateway is unreachable and nothing can correct it.
        """
        key = self._resolve_key(game_id, provider)
        previous = self._synced.get(key) or {}
        keep = url if isinstance(url, str) and url.startswith("https://") else ""
        self._synced[key] = {
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
        tracked: Dict[str, int] = {}
        for key, entry in self._synced.items():
            app_id = as_id((entry or {}).get("app_id"))
            if app_id is not None:
                tracked[str(key)] = app_id

        # Grouped by game AND provider, which is the whole point.
        #
        # Grouped by game alone, the caller deletes every tile in a group but
        # one — and with sync_all_providers on, a game's Boosteroid, Luna and
        # GeForce NOW tiles ARE one group. Two of the three would be destroyed
        # on every sync, taking their playtime and controller layout with them.
        # Two tiles for the same game on the SAME service are still twins, and
        # those are what this is for.
        by_route: Dict[str, Dict[str, Any]] = {}
        for entry in found:
            provider = entry.get("provider") or ""
            key = _tracking_key(entry["game_id"], provider) if provider \
                else str(entry["game_id"])
            row = by_route.setdefault(key, {"game_id": entry["game_id"],
                                            "provider": provider,
                                            "app_ids": []})
            row["app_ids"].append(entry["app_id"])

        return {
            "ok": True,
            "shortcuts": [
                {
                    "key": key,
                    "game_id": row["game_id"],
                    "provider": row["provider"],
                    "app_ids": row["app_ids"],
                    # Keep the one already tracked; failing that the oldest,
                    # which is the one likelier to hold playtime.
                    "keep": tracked[key] if tracked.get(key) in row["app_ids"]
                            else row["app_ids"][0],
                    "tracked": tracked.get(key),
                }
                for key, row in by_route.items()
            ],
        }

    async def forget_shortcut(self, game_id: Any) -> Dict:
        key = str(game_id)
        tracked = self._synced.pop(key, None) or {}
        numeric = _key_game_id(key)
        # The tile is gone, so its icon file is dead weight. Left alone these
        # accumulate forever: one per game ever added, never read again.
        #
        # Only once the LAST tile for this game is gone. With one tile per
        # service they share the game's artwork, so removing the Boosteroid
        # copy of a game still on GeForce NOW must not take the icon the other
        # tile is drawn with.
        if numeric is not None and not any(
                _key_game_id(other) == numeric for other in self._synced):
            self._remove_icon_files(numeric, tracked.get("app_id"))
        await self._save_state()
        return {"ok": True}

    async def save_icon(self, game_id: int, data: str, kind: str,
                        app_id: Optional[int] = None) -> Dict:
        """Write an image to disk and return the path, for SetShortcutIcon.

        Steam takes the four library slots as bytes but takes an icon as a
        filesystem path, so this is the one piece of artwork that has to exist
        as a file. The frontend already holds the bytes — it fetched them for
        the icon slot — so they are passed through rather than downloaded a
        second time.

        Named after the SHORTCUT's app id, not the game id, and that matters
        now the file lands in Steam's own grid folder. Steam names its library
        art `<appid>.png` in that same directory, and our game ids start at 1 —
        so `23101.png` is both our game 23101 and the header art of whatever
        Steam app happens to have id 23101. Writing one would silently destroy
        the other. A shortcut app id is a large generated number and carries
        the `_icon` suffix every other tool uses, so it collides with nothing.
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

        path = os.path.join(_icon_dir(), _icon_name(game_id, app_id, kind))
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

    def _download_custom_layout(self, name: str) -> Optional[str]:
        """Fetch a custom VDF from the gateway, at most once every ten minutes.

        Cached because install_controller_layout runs for EVERY tile on every
        sync, and a library with 137 Boosteroid tiles therefore asked the
        gateway for the same unchanged file 137 times in a row, serially. At
        85 ms a round trip that is eleven seconds of a sync spent downloading
        one file over and over — measured, and the whole of the "sync got slow
        again" report.

        Ten minutes rather than forever: a layout edited in the admin should
        reach a device without anyone restarting anything, and one request per
        ten minutes instead of 137 per sync is already the entire win.
        """
        cached = _LAYOUT_CACHE.get(name)
        if cached and (time.time() - cached[0]) < _LAYOUT_CACHE_TTL:
            return cached[1]
        gateway = self._gateway()
        url = f"{gateway}/api/controller-layout/{urllib.parse.quote(name)}"
        token = self._settings.get("api_token", "")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": ARTWORK_USER_AGENT,
        })
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=REQUEST_TIMEOUT) as resp:
                # Bounded, like _request and fetch_artwork already are. This
                # was the one read that trusted whatever answered to be
                # reasonable about size — a controller layout is a few
                # kilobytes of text, and the whole of it is held in memory and
                # then in a module-level cache.
                raw = resp.read(LAYOUT_MAX_BYTES + 1)
                if len(raw) > LAYOUT_MAX_BYTES:
                    raise ValueError(
                        f"layout {name!r} is larger than {LAYOUT_MAX_BYTES} bytes")
                text = raw.decode("utf-8", errors="replace")
        except Exception as exc:
            decky.logger.warning("custom layout download failed: %s", exc)
            # A stale copy beats no layout at all: the gateway being briefly
            # unreachable should not strip a layout off every tile.
            return cached[1] if cached else None
        _LAYOUT_CACHE[name] = (time.time(), text)
        return text

    async def install_controller_layout(self, shortcut_name: str,
                                        template_url: str) -> Dict:
        """Copy a Steam Input template into the per-game config directory.

        Non-Steam shortcuts use the SHORTCUT NAME as the directory, not the
        app ID. Verified on hardware: the existing configs on this Deck sit
        under names like "xbox cloud gaming" and "tiktok", not numeric IDs.

        Accepts two URL schemes:
          template://  — a built-in Steam template from controller_base/templates/
          custom://    — a VDF hosted on the gateway, downloaded to the device
        """
        if not isinstance(shortcut_name, str) or not shortcut_name.strip():
            return {"ok": False, "error": "bad shortcut_name"}
        if not isinstance(template_url, str):
            return {"ok": False, "error": "bad template_url"}
        # The name becomes a DIRECTORY, and it is built from the game's title
        # in the catalogue — which is scraped from each provider's own API and
        # is not ours to trust. A title containing a separator or a `..` would
        # have this creating directories and writing files anywhere the plugin
        # can reach: os.path.join(user_path, "../../x") leaves user_path
        # without complaint. Checked here AND again on the resolved path
        # below, because this one is easy to reason about and that one cannot
        # be fooled by anything clever.
        if (os.sep in shortcut_name or (os.altsep and os.altsep in shortcut_name)
                or shortcut_name.strip(".") == ""):
            decky.logger.warning("refusing a layout directory named %r", shortcut_name)
            return {"ok": False, "error": "unsafe shortcut_name"}

        steam_root = os.path.realpath(os.path.expanduser("~/.steam/steam"))

        if template_url.startswith("custom://"):
            vdf_text = await asyncio.to_thread(self._download_custom_layout,
                                               template_url.removeprefix("custom://"))
            if vdf_text is None:
                return {"ok": False, "error": "could not download custom layout"}
        elif template_url.startswith("template://"):
            filename = template_url.removeprefix("template://")
            source = os.path.join(steam_root, "controller_base", "templates", filename)
            if not os.path.isfile(source):
                return {"ok": False, "error": f"template not found: {filename}"}
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                vdf_text = f.read()
        else:
            return {"ok": False, "error": "unsupported URL scheme"}

        # Rewrite the url field to point to the actual destination. Custom VDFs
        # arrive with whatever url the author set (often a path from a different
        # project), and built-in templates have none at all.
        import re as _re
        vdf_text = _re.sub(r'"url"\s+"[^"]*"', '"url"\t\t"__PLACEHOLDER__"', vdf_text)
        if '"url"' not in vdf_text:
            vdf_text = vdf_text.replace(
                '"controller_mappings"\n{',
                '"controller_mappings"\n{\n'
                '\t"url"\t\t"__PLACEHOLDER__"\n'
                '\t"export_type"\t\t"template"',
                1)

        installed = 0
        autosave_url = None
        configs_root = os.path.join(
            steam_root, "steamapps", "common", "Steam Controller Configs")
        for user_dir in os.listdir(configs_root):
            user_path = os.path.join(configs_root, user_dir, "config")
            if not os.path.isdir(user_path):
                continue
            dest_dir = os.path.join(user_path, shortcut_name)
            # Belt and braces: resolve it and confirm it really is inside the
            # per-user config directory. The check above reads clearly and
            # catches the obvious shapes; this one catches whatever it did
            # not, and it is the one that has to be right.
            if not os.path.realpath(dest_dir).startswith(
                    os.path.realpath(user_path) + os.sep):
                decky.logger.warning(
                    "layout directory for %r resolves outside %s; skipping",
                    shortcut_name, user_path)
                continue
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, "controller_neptune.vdf")
            try:
                final = vdf_text.replace("__PLACEHOLDER__", f"autosave://{dest}")
                installed += 1
                autosave_url = f"autosave://{dest}"
                # Only write when it would change something. This runs for
                # every tile on every sync across every Steam user directory,
                # so on this Deck that was 137 tiles x 4 directories = 548
                # rewrites of files that already said exactly this.
                try:
                    with open(dest, "r", encoding="utf-8", errors="replace") as f:
                        unchanged = f.read() == final
                except OSError:
                    unchanged = False
                if not unchanged:
                    with open(dest, "w", encoding="utf-8") as f:
                        f.write(final)
                    decky.logger.info("controller layout: wrote %s", dest)
                _register_configset(user_path, shortcut_name)
            except OSError as exc:
                decky.logger.warning("controller layout copy failed: %s", exc)
        return {"ok": installed > 0, "installed": installed,
                "autosave_url": autosave_url if installed else None}

    async def tracked_providers(self) -> Dict[str, Any]:
        """app id -> which service that tile launches, for the library badge.

        Its own call rather than reading the whole library: the badge runs on
        a patched Steam route, once per game page opened, and asking the
        gateway for several hundred games to learn one word would be absurd.
        This reads only what is already in memory.

        Keys are strings because they cross to JavaScript, where a number that
        large is fine but an object key is a string regardless — returning
        ints would make the frontend convert them back on every lookup.
        """
        out: Dict[str, str] = {}
        for entry in self._synced.values():
            if not isinstance(entry, dict):
                continue
            app_id = entry.get("app_id")
            provider = entry.get("provider")
            if isinstance(app_id, int) and isinstance(provider, str) and provider:
                out[str(app_id)] = provider
        return {"ok": True, "providers": out}

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

    def _detected_display(self) -> str:
        """What the panel shows under the scale dropdown.

        The same answer the launcher will act on, from the same code, so the
        line in the panel and the line in the launch log cannot disagree —
        and so "Auto" is not a number the user has to take on trust.

        Cached, because this shells out to xrandr and the panel polls status
        every 1.5 seconds while a sync runs: uncached, the eleven-minute sync
        measured here would have spawned four hundred processes to answer a
        question whose answer does not change. A display is docked or
        undocked in seconds, never in milliseconds, so a short life is plenty.

        The launcher does NOT share this cache. It probes once per launch,
        where the cost is nothing and the answer has to be current.
        """
        now = time.monotonic()
        when, answer = self._display_cache
        if answer and now - when < DISPLAY_CACHE_SECONDS:
            return answer
        try:
            answer = display.describe(self._settings.get("ui_scale", "auto"))
        except Exception as exc:                      # never break the panel
            answer = f"could not detect the display ({exc})"
        self._display_cache = (now, answer)
        return answer

    def _sync_all_providers(self) -> bool:
        """Make a tile per service a game is on, instead of just the best one.

        Off unless explicitly turned on. It multiplies the library — measured
        here, 387 tiles become 636 — and most people want the one route their
        preferred service gives them, so this is a choice someone makes rather
        than one they have made for them.
        """
        return self._settings.get("sync_all_providers") is True

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

    @staticmethod
    def _version() -> str:
        return decky.DECKY_PLUGIN_VERSION or "unknown"

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
            "sync_all_providers": self._sync_all_providers(),
            "ui_scale": self._settings.get("ui_scale", "auto"),
            "ui_scale_detected": await asyncio.to_thread(self._detected_display),
            # On unless turned off: a badge that says where a tile streams
            # from is the point of having the tiles look different at all.
            "badge_enabled": self._settings.get("badge_enabled") is not False,
            # The mark on the highlighted card in the home carousel and the
            # grid. Its own switch rather than sharing one with the game page:
            # they are two places, and wanting one without the other is a
            # perfectly ordinary thing to want.
            "badge_on_cards": self._settings.get("badge_on_cards") is not False,
            "badge_position": (self._settings.get("badge_position")
                               if self._settings.get("badge_position")
                               in self.BADGE_POSITIONS else "bl"),
            "last_error": self._last_error,
            # Only while a sign-in is in flight; the browser that
            # asks for this closed the panel that first showed it.
            "pending_login_code": (
                self._pending_login_code
                if self._login_task is not None and not self._login_task.done()
                else ""
            ),
            "pending_login_url": (
                self._pending_login_url
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
    ALLOWED_SETTINGS = {"browser", "sync_minutes", "sync_all_providers", "ui_scale",
                        "badge_enabled", "badge_on_cards", "badge_position"}

    # Where the provider badge sits on a game's page. Validated as a set
    # rather than trusted: this value is written into a style, and a dropdown
    # is not the only thing that can call set_setting.
    BADGE_POSITIONS = ("tl", "tr", "bl", "br")

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key not in self.ALLOWED_SETTINGS:
            decky.logger.warning("Rejected setting key: %s", key)
            return await self.get_status()
        # badge_position is written into a CSS rule on a patched Steam page,
        # so it is checked against the list rather than taken on trust. The
        # dropdown can only send one of four values; set_setting is an RPC and
        # the dropdown is not the only thing that can call it.
        if key == "badge_position" and value not in self.BADGE_POSITIONS:
            decky.logger.warning("Rejected badge position: %r", value)
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
