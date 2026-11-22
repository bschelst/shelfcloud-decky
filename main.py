# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Shelfcloud — Decky plugin backend.

A sync agent, not a game browser: games are picked on the web, this keeps the
Deck's Steam library matching. Python does the network, credentials, disk state
and the poll loop; the frontend does anything that touches SteamClient. So this
side decides what the library should hold and emits an event, and the frontend
makes the shortcuts match. Stdlib only, Decky doesnt promise extra packages.
"""

import asyncio
import base64
import functools
import hashlib
import json
import os
import random
import re
import shutil
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

# No install started. also the shape the frontend reads, so the keys are always
# there and it does not have to check each one.
IDLE_INSTALL = {
    "running": False, "app_id": None, "label": None,
    "message": "", "error": None, "done": False,
}
REQUEST_TIMEOUT = 20
SYNC_EVENT = "shelfcloud/library"

# Custom layout VDFs, by name: {name: (fetched_at, text)}. See
# _download_custom_layout.
_LAYOUT_CACHE: Dict[str, Any] = {}
_LAYOUT_CACHE_TTL = 600

# path -> (etag, raw json text). The library is polled on a timer but changes
# hardly at all, so without this the same few hundred KB came down every sync to
# say nothing changed. raw text, not the parsed object: two callers sharing one
# dict would see each other's edits, and re-parsing is cheaper than the transfer.
_ETAG_CACHE: Dict[str, Any] = {}
_ETAG_CACHE_MAX = 8

# Must match backend/providers.py LAYOUT_GAMEPAD and LAYOUT_WEB_BROWSER.
_LAYOUT_GAMEPAD = "template://controller_neptune_gamepad_fps.vdf"
_LAYOUT_WEB_BROWSER = "template://controller_neptune_webbrowser.vdf"


# A provider key that survives a shell-quoted command line unchanged.
_PLAIN_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Controller types we write a config for, so plugging a pad in just works. A
# config for a pad nobody owns costs a few KB and is ignored; a MISSING one lets
# Steam fall back to its default and the service's shortcuts become unreachable.
# neptune is first and listed on purpose, not assumed: the loop skips it when it
# is already the type the layout was made for, but a custom VDF from an Xbox pad
# would else leave the one controller this plugin exists for with no config.
CONTROLLER_TYPES = ("neptune", "ps5", "ps4", "xbox360", "xboxone",
                    "switch_pro", "generic")

# controller_neptune_gamepad_fps.vdf -> ("neptune", "gamepad_fps"). Anchored on
# the known type names: several contain an underscore, so splitting on the first
# or last one gets switch_pro wrong.
_re_flavour = re.compile(
    r"^controller_(neptune|ps3|ps4|ps5|xbox360|xboxone|switch_pro|generic|android|apple)"
    r"_(.+)\.vdf$")

# Fallback when a type has no template for the flavour we wanted. Not every type
# ships every flavour, so this is ordered best first.
FLAVOUR_CHAIN = ("gamepad+mouse", "gamepad_fps", "gamepad_joystick", "wasd")


def _best_template(steam_root: str, controller_type: str, flavour: str) -> Optional[str]:
    """Valve's own template for this pad, as close to `flavour` as it ships."""
    tdir = os.path.join(steam_root, "controller_base", "templates")
    wanted = [flavour] + [f for f in FLAVOUR_CHAIN if f != flavour]
    for candidate in wanted:
        path = os.path.join(tdir, f"controller_{controller_type}_{candidate}.vdf")
        if os.path.isfile(path):
            return path
    return None


def _safe_layout_name(shortcut_name: str) -> bool:
    """Whether a shortcut's name can be used as its config directory.

    Steam treats the title as a path, so a plain slash nests and Steam reads it
    back the same way -- that is fine. Only a part that means something to the
    filesystem is refused: ".." climbs, "" is a leading or doubled slash (join()
    on an absolute path throws away everything before it), "." is the directory
    itself. The resolved-path controle at the destination is the one that has to be
    right; this just makes the refusal early and clear.
    """
    if not shortcut_name or not shortcut_name.strip():
        return False
    parts = shortcut_name.split(os.sep)
    if os.altsep:
        parts = [bit for part in parts for bit in part.split(os.altsep)]
    return all(part not in ("", ".", "..") for part in parts)


def _sized(fn):
    """Log a reply big enough to close Decky's websocket.

    aiohttp closes the connection on a message over 4 MB, with no error on either
    side -- so the plugin does not fail, it just stops, and every later call
    waits for an answer that never comes. This names the reply instead of leaving
    us to guess which one it was.
    """
    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs):
        reply = await fn(self, *args, **kwargs)
        try:
            size = len(json.dumps(reply, default=str))
            if size > 512 * 1024:
                decky.logger.warning(
                    "reply %s is %.2f MB%s", fn.__name__, size / 1048576.0,
                    "  <-- OVER aiohttp's 4 MB limit, this closes the socket"
                    if size > 4 * 1024 * 1024 else "")
        except Exception:
            pass
        return reply
    return wrapper


# One connection pool for every call this plugin makes. urlopen opens a new TCP
# connection, plus a TLS handshake, for every request, and a sync makes hundreds
# of them to the same two or three hosts. urllib3 keeps them alive and hands the
# same one back. Decky already ships it, so no new dependancy.
try:
    import urllib3 as _urllib3
except Exception:                       # pragma: no cover - urllib3 is present
    _urllib3 = None

_POOL = None


def _http():
    """The shared pool, opened on first use."""
    global _POOL
    if _POOL is None:
        _POOL = _urllib3.PoolManager(
            # A few live connections is plenty: calls go out a few at a time, and
            # a bigger pool would just hold sockets open for work not coming.
            maxsize=4,
            block=False,
            # Redirects yes, retries no. A CDN moving an image is normal; a retry
            # is not ours to decide -- callers already treat a failure as "skip
            # this one", and a quiet second try would double a slow call's cost.
            retries=_urllib3.Retry(total=0, redirect=5, raise_on_redirect=False,
                                   raise_on_status=False),
            ssl_context=SSL_CTX,
            timeout=_urllib3.Timeout(total=REQUEST_TIMEOUT),
        )
    return _POOL


class _PooledResponse:
    """A urllib3 response wearing the shape urlopen's callers expect."""

    def __init__(self, raw):
        self._raw = raw
        self.headers = raw.headers
        self.status = raw.status

    def read(self, amount=None):
        return self._raw.read(amount) if amount is not None else self._raw.read()

    def close(self):
        try:
            self._raw.release_conn()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Refuse redirects on the urllib fallback path.

    urllib re-sends every header to the new location, Authorization included, so
    one redirect off the gateway would hand our API token to whatever answered.
    Returning None makes urllib raise instead of following.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open(url: str, headers: Optional[Dict[str, str]] = None, method: str = "GET",
          body: Optional[bytes] = None, timeout: Optional[float] = None):
    """A pooled request that fails the way urlopen does.

    Raising HTTPError on a non-2xx is the point. Every caller here already
    handles that shape -- 304 means "what you hold is current", 401 means "sign
    in again" -- and rewriting all of it around urllib3's status-in-the-response
    would be a second change riding on a first.
    """
    if _urllib3 is None:                # pragma: no cover
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=headers or {})
        # No redirects here either: urllib re-sends every header, so a redirect
        # off the gateway would leak our API token to whatever answered.
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=SSL_CTX), _NoRedirects)
        return opener.open(request, timeout=timeout or REQUEST_TIMEOUT)

    try:
        # NEVER None. urllib3 reads timeout=None as "wait for ever" and it
        # overrides the pool default, so a call without an explicit one loses its
        # deadline. A stalled read then holds a thread-pool worker for good, and
        # after four of those every asyncio.to_thread queues up and the backend
        # answers nothing. The socket stays open, so it looks like a transport
        # problem for hours.
        raw = _http().request(
            method, url, body=body, headers=headers or {},
            preload_content=False,
            timeout=_urllib3.Timeout(total=timeout or REQUEST_TIMEOUT),
        )
    except _urllib3.exceptions.HTTPError as exc:
        # Translated, not passed through. urllib3 raises MaxRetryError/SSLError
        # where urlopen raised URLError, and the callers here expect URLError --
        # _request's handler that logs a certificate failure catches URLError, so
        # an untranslated urllib3 error would slip past it. Wrapped, not reraised
        # bare, so the original stays attached for anything that looks.
        raise urllib.error.URLError(exc) from exc
    if not (200 <= raw.status < 300):
        raw.read()                      # drain, so the connection can be reused
        raw.release_conn()
        raise urllib.error.HTTPError(url, raw.status, str(raw.reason),
                                     raw.headers, None)
    return _PooledResponse(raw)


# Characters Steam removes from a shortcut name before using it as a directory:
# everything Windows forbids in a filename, plus the full stop. Checked against
# Steam's own answer for all 840 tiles on this Deck.
_STEAM_STRIPS = '\\/:*?"<>|.'

def _layout_key(shortcut_name: str) -> str:
    """The name Steam uses for a shortcut's controller config, LOWERCASED.

    Steam lower-cases a non-Steam shortcut's name for both the per-game config
    directory and the configset entry. On a case-sensitive filesystem our old
    "Assetto Corsa Rally (Boosteroid)" and Steam's "assetto corsa rally
    (boosteroid)" are two different directories, so Steam never saw a layout we
    wrote. Lower case is not the whole rule though: Steam also strips the
    characters Windows forbids in a filename, and the dot with them
    ("R.E.P.O. (Boosteroid)" -> "repo (boosteroid)"). This rule reproduces all
    840 tiles on this Deck exactly. A nice side effect: "/" goes too, so a title
    can no longer nest a directory.
    """
    return "".join(c for c in shortcut_name.lower() if c not in _STEAM_STRIPS)


def _register_configset(config_dir: str, app_key: str,
                        controller_type: str = "neptune") -> None:
    """Register a per-game config in the configset file so Steam finds it.

    Without this entry Steam ignores the VDF in the per-game directory. One
    configset per controller type, and it must match the config it registers:
    writing controller_ps5.vdf but only touching configset_controller_neptune.vdf
    leaves the PS5 config unused, which looks just like the layout not working.
    """
    configset = os.path.join(config_dir,
                             f"configset_controller_{controller_type}.vdf")
    if not os.path.isfile(configset):
        return
    with open(configset, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    # A quote or a brace in the key would end the entry and open a new block. The
    # key comes from a game name scraped from each provider's API, so it is not
    # ours to trust. _layout_key strips the quote but braces get through, and this
    # file is account-wide -- a bad entry breaks the parse for every entry after.
    if any(ch in app_key for ch in '"{}\n\r\x00'):
        decky.logger.warning("refusing a configset key containing markup: %r",
                             app_key)
        return
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

# Artwork is fetched here, not in the browser; see fetch_artwork. The hardware
# goes in the comment field so the gateway can name this device in a list.
ARTWORK_USER_AGENT = (
    f"Shelfcloud/0.1 ({_hardware_label()}; +https://github.com/bschelst/shelfcloud)")
ARTWORK_MAX_BYTES = 8 * 1024 * 1024

# Bumped to force one pass that re-selects every layout, ignoring the "leave a
# user's own choice alone" rule. That rule can't tell a real choice from the
# broken state we used to write (both are eSelectionType 1 naming an autosave
# file in the tile's own directory), so it kept protecting the bug. Raising this
# repairs once and then gets out of the way; a later choice is respected again.
# 2->3: the gateway layouts start in Gamepad now (revision 42). A changed layout
# only reaches a tile that gets touched, so without this every existing tile
# kept the old file and still started in Mouse+Keyboard.
LAYOUT_REPAIR = 3

# One artwork slice. aiohttp closes the websocket on a message over 4 MB, and a
# 7 MB Steam hero is 9 MB of base64 -- so an image goes in slices and the
# frontend joins them. Sized small on purpose: the freezes came from a burst of
# a few 1-2 MB replies at once, so many small messages beat a few big ones.
ARTWORK_CHUNK_CHARS = 192 * 1024

# The most any gateway reply may be. Generous -- the biggest is the whole
# catalogue, a few MB -- but not unbounded: a captive portal or broken proxy
# streaming bytes should fail, not fill the Deck's memory.
RESPONSE_MAX_BYTES = 64 * 1024 * 1024

# A controller layout is a few KB of VDF text, the biggest we ship under 20 KB.
# A megabyte is already far past anything real, and this one sits in a cache.
LAYOUT_MAX_BYTES = 1024 * 1024

# How many library entries one change may carry. The gateway refuses more than
# 500 and a cancelled bulk add is often bigger, so the caller splits rather than
# losing the tail to a 422.
_LIBRARY_BATCH = 200

# How often to re-check the token with the gateway. Short enough to notice a
# revoked session quickly, long enough not to be chatty.
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


# How long the panel may reuse the last display probe. Docking takes seconds; the
# panel asks every 1.5 of them.
DISPLAY_CACHE_SECONDS = 15.0


def _tracking_key(game_id: int, provider: str) -> str:
    """How a tile is identified in synced.json.

    It used to be the game id alone, which was fine while a game had one tile.
    With sync_all_providers on it is not: Fortnite gets a tile per service, all
    carrying game id 42, and a map keyed by game id holds only one -- the rest
    become orphans nothing cleans up. Composite whether or not the setting is on,
    so flipping the toggle does not orphan every tile.
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
    """Steam's own grid folder for the signed-in account, if found.

    Picked by the newest shortcuts.vdf: a Deck can hold several accounts, and the
    one being written to is the one in use.
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

    `<app_id>_icon.<kind>` when the shortcut's app id is known, the shape every
    other tool on a Deck writes into Steam's grid folder. The game-id form is the
    old name, kept only so icons from an older version can still be found and
    removed.
    """
    if app_id:
        return f"{int(app_id)}_icon.{kind}"
    return f"{int(game_id)}.{kind}"


def _icon_dir() -> str:
    """Where shortcut icons live on disk.

    An icon is stored in shortcuts.vdf as a PATH that Steam re-reads every time it
    draws it, so the file must be durable, NOT the plugin directory that
    install-to-deck.sh overwrites. Steam's own grid folder first: an icon in the
    settings dir rendered as a broken image even as a valid PNG, because Steam's UI
    is a browser process and a path outside its own tree is the difference. The
    settings dir stays as the fallback for when Steam's folder can't be found.
    """
    return _steam_grid_dir() or _settings_icon_dir()


def as_id(value: Any) -> Optional[int]:
    """An integer id out of JSON, or None if it is anything else.

    JSON promises nothing about types: an id can arrive as a string from a proxy
    that rewrites bodies, a float from a language whose numbers are all floats, or
    a dict from an error document. Everything downstream wants an int or nothing,
    so convert once, here. bool is rejected before int because it is a subclass of
    int -- without that line `True` quietly becomes the id 1, wich is a real shelf.
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

    Only a missing id drops the row, since an id is all a selection sends. A
    missing name gets a made-up one instead of dropping the row: the device may
    point at this shelf, and a picker whose current value is not in its options
    draws empty -- which looks like the plugin is broken, not one odd shelf.
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
        # game_id -> {"app_id": int, "name": str, "launch": str}. Without it a
        # game removed on the web could never be found and removed here, and
        # re-syncing would make duplicate tiles.
        self._synced: Dict[str, Dict[str, Any]] = {}
        # One-off maintenance, done on the first library read of a session.
        self._pruned_layouts = False
        # Tracked tiles shortcuts.vdf did not mention last time. Forgetting one
        # takes two misses in a row -- see _forget_vanished_tiles.
        self._vanish_suspects: set = set()
        # (when, answer) for the display probe, which shells out to xrandr and is
        # asked for by a panel that polls status every 1.5 seconds.
        self._players_cache: Dict[str, Any] = {}
        # (when, answer) of the last GOOD count per game, kept next to the cache
        # so a failed ask can fall back on it. see current_players
        self._players_good: Dict[str, Any] = {}
        self._display_cache: tuple = (0.0, "")
        self._poll_task: Optional[asyncio.Task] = None
        self._login_task: Optional[asyncio.Task] = None
        # Pairing code for a sign-in in progress, shown in the panel so the person
        # can read it while the browser asks for it.
        self._pending_login_code: str = ""
        self._pending_login_url: str = ""
        self._last_error: Optional[str] = None
        self._last_sync: Optional[float] = None
        # Whether the gateway last accepted our token. A token can stop working on
        # its own, so "we have a string on disk" is not "we are signed in".
        self._auth_ok: bool = True
        self._auth_checked: float = 0.0
        # Progress of a browser install, read by the panel through get_status so it
        # survives the panel being closed mid-download.
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
        # The icons in Steam's grid folder STAY. Decky calls this on every update
        # too (store updates uninstall the old version first), and the tiles stay
        # in the library through that. Deleting their icons is what left 377
        # tiles with a grey circle after the 0.3.7 update, and it came back with
        # every update since. An icon of a tile that is really gone is removed
        # with the tile, see forget_shortcut.

        # The old home really was ours alone, so it can still go wholly.
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

        Both directories and both naming schemes: an icon written by an earlier
        version sits on disk under the old name and would else be left behind.
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
        """Which tiles this plugin has made -- recovered from Steam if lost.

        A plain _read_json returns {} for a missing or unreadable file, and reconcile
        reads {} as "never made any tiles", so every game gets a new tile beside the
        old untracked one -- duplicates on app ids that carry playtime, collections
        and artwork. shortcuts.vdf is the authority on what Steam has, and every tile
        carries "--game-id N", so rebuilding from it recovers the real app ids and
        leaves user-deleted tiles deleted.
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

        One-way and idempotent: a key that already has a colon is left alone, so
        this runs on every load and only does work once. An entry with no provider
        keeps its bare key -- guessing a wrong provider is worse, the tile would
        stop matching anything and be deleted, losing its playtime and artwork. As
        is, the frontend's compatibility lookup finds it and re-keys it on the
        next write.
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
        return Plugin._drop_bare_leftovers(migrated)

    @staticmethod
    def _drop_bare_leftovers(state: Dict) -> Dict:
        """Drop a bare-key record with no provider when a "id:provider" record
        holds the same shortcut. An older build left these behind, empty, and
        nothing removed them: the frontend writes composite keys only, and
        _resolve_key only drops a bare key that is written to. They counted
        each tile twice, as "Other" in the panel. Same app id only, a bare
        record for an other shortcut is a real tile and stays."""
        def game_of(key: Any) -> str:
            return str(key).split(":", 1)[0]

        taken = {(game_of(key), value.get("app_id")) for key, value in state.items()
                 if ":" in str(key) and isinstance(value, dict)}
        kept = {key: value for key, value in state.items()
                if ":" in str(key) or not isinstance(value, dict)
                or value.get("provider")
                or (str(key), value.get("app_id")) not in taken}
        dropped = len(state) - len(kept)
        if dropped:
            decky.logger.info("dropped %d leftover tracking record(s) of tiles "
                              "already tracked by game and provider", dropped)
        return kept

    @staticmethod
    def _recover_from_steam() -> Dict:
        """game id -> {app_id}, read back out of Steam's own shortcuts.vdf.

        Only the app id is recovered, because only it can't be replaced -- name,
        exe and launch options are rewritten on every sync anyway.
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
            # matched, not recreated beside itself. A tile from a build that wrote
            # no --provider keeps the bare game id; the frontend still finds it.
            key = _tracking_key(entry["game_id"], provider) if provider \
                else str(entry["game_id"])
            # setdefault: Steam's order is oldest first (see duplicates()), and the
            # oldest tile is the one holding the playtime and the layout.
            found_entry = {"app_id": entry["app_id"]}
            if provider:
                found_entry["provider"] = provider
            recovered.setdefault(key, found_entry)
        return recovered

    @staticmethod
    def _read_json(path: str) -> Dict:
        """Read a JSON object from disk, or {} for anything else at all.

        Two things this used to get wrong, both taking the whole plugin down
        rather than the one file. It caught only FileNotFoundError and
        JSONDecodeError, but a wrong mode raises PermissionError, a corrupted byte
        UnicodeDecodeError, a directory IsADirectoryError -- any of them reached
        _main and stopped the plugin loading. And it returned whatever parsed, so
        `[]` made self._settings a list and every .get() raised AttributeError.
        Returning {} is right for both: an unreadable settings file means "no
        settings", and _load_state already treats {} as "ask Steam instead".
        """
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError) as exc:
            # ValueError covers JSONDecodeError and UnicodeDecodeError; OSError
            # covers missing, unreadable and not-a-file.
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
        # Write-then-rename so a crash mid-write cant leave unreadable state.
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)

        # 0600, because settings.json holds the bearer token. Decky drops to
        # `deck` for all plugin backends, so at the default 0644 any other plugin
        # could read this file and walk off with the credential. Set on the temp
        # file before the rename so the final file is never briefly world-readable.
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

        # Only a plain GET is safe to revalidate: a POST has a side effect and
        # must never be answered from the cache. Keyed on the full path, so
        # ?shelf=... is a different entry because it is a different answer.
        conditional = method == "GET" and data is None
        cached = _ETAG_CACHE.get(path) if conditional else None
        if cached:
            request.add_header("If-None-Match", cached[0])
        if authenticated:
            token = self._token()
            if not token:
                raise PermissionError("not signed in")
            request.add_header("Authorization", f"Bearer {token}")

        try:
            # Pooled. See _open: urlopen opened a fresh TCP connection and TLS
            # handshake per call, and a sync makes hundreds of them.
            with _open(url, headers=dict(request.header_items()),
                       method=method, body=data) as response:
                # Bounded, like fetch_artwork. The biggest thing this asks for is
                # a whole catalogue, a few MB; reading one byte past the cap tells
                # "at the limit" from "over it".
                raw = response.read(RESPONSE_MAX_BYTES + 1)
                if len(raw) > RESPONSE_MAX_BYTES:
                    raise ValueError(
                        f"gateway sent more than {RESPONSE_MAX_BYTES} bytes")
                payload = raw.decode()
                self._auth_ok = True
                if conditional:
                    etag = response.headers.get("ETag")
                    if etag:
                        # Cleared wholesale, not one by one: there are only a few
                        # GET paths, so if this ever fills up something is making
                        # keys and the right answer is to drop them all.
                        if (len(_ETAG_CACHE) >= _ETAG_CACHE_MAX
                                and path not in _ETAG_CACHE):
                            _ETAG_CACHE.clear()
                        _ETAG_CACHE[path] = (etag, payload)
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            # 304 is the gateway agreeing that what we hold is current. urllib
            # raises for it because it is not a 2xx, so it lands here.
            if exc.code == 304 and cached:
                self._auth_ok = True
                return json.loads(cached[1]) if cached[1] else None
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
        since Game Mode has no browser of its own."""
        self._pending_login_code = ""
        self._pending_login_url = ""
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        try:
            result = await asyncio.to_thread(
                self._request, "/auth/session?web=0", "POST", None, False
            )
            # The pairing code stops someone else finishing this sign-in. Kept
            # here, not just returned once: opening Steam's browser closes the
            # panel, and the user still has to read the code off the panel while
            # the browser asks for it, so a value living only in the panel is gone.
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

        This has to live in the backend. Signing in opens Steam's browser, which
        closes the Quick Access panel; the panel is a React component, so
        unmounting it killed the timer polling for the result. Steam signed the
        user in fine every time, but nobody was left to collect the token. Same
        lesson as the background sync: what must outlive the panel can't live in it.
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

            # Paired. The code has done its job and should not linger in the
            # panel for the next person who opens it.
            self._pending_login_code = ""
            api_token = result.get("api_token")
            if not api_token:
                decky.logger.warning("Sign-in response missing api_token")
                continue
            self._settings["api_token"] = api_token
            self._settings["steam_id64"] = result.get("steam_id64")
            # A fresh credential clears any cached "not signed in" answer.
            self._auth_ok = True
            self._auth_checked = 0.0
            await self._save_settings()
            decky.logger.info("Signed in as %s", result.get("steam_id64"))
            # Fill the library straight away rather than waiting a tick.
            await decky.emit(SYNC_EVENT)
            return

        self._pending_login_code = ""
        decky.logger.info("Sign-in was not completed within the time limit")

    async def poll_login(self, session_token: str) -> Dict[str, Any]:
        """Progress of the sign-in the backend is waiting on.

        The backend does the claiming -- a session token is single use, so two
        claimants would race and one would be told the link was already used. This
        only reports.
        """
        if self._token():
            return {"ok": True, "status": "signed_in",
                    "steam_id64": self._settings.get("steam_id64")}
        waiting = self._login_task is not None and not self._login_task.done()
        return {"ok": True, "status": "pending" if waiting else "idle"}

    def _clear_session_state(self) -> None:
        """Drop everything tied to the account that was signed in.

        There are two ways to stop being signed in -- the gateway rejecting the
        token, and the user pressing Sign out -- and each used to clear this by
        hand. They drifted: the ETag cache was cleared on the first path only, so
        a real sign-out left the old account's library in memory, the one case
        where somebody actually asked for it to go. One place, so nothing is missed.
        """
        self._settings.pop("api_token", None)
        self._settings.pop("steam_id64", None)
        self._auth_ok = False
        self._auth_checked = 0.0
        _ETAG_CACHE.clear()

    def _forget_credentials(self) -> None:
        """Drop a credential the gateway no longer accepts."""
        self._clear_session_state()
        try:
            self._write_json(_settings_file(), self._settings)
        except OSError:
            pass   # in-memory state is already right; disk catches up later

    async def logout(self) -> Dict[str, Any]:
        try:
            await asyncio.to_thread(self._request, "/api/me/logout", "POST", {})
        except Exception:
            pass  # local sign-out must work even when the gateway is down
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        self._clear_session_state()
        await self._save_settings()
        return {"ok": True}

    # --------------------------------------------------------------- library

    @staticmethod
    def _artwork(game: Dict) -> Dict[str, Optional[str]]:
        """Artwork for each Steam library slot, as resolved by the gateway.

        No lookups here: artwork is catalog data, resolved once server-side for
        every user, so the plugin only renders what it is given. Falls back to
        provider images for games the gateway could not resolve.
        """
        def url(*keys) -> Optional[str]:
            """First value that is a usable URL, else None.

            https only, checked when the value is first accepted. fetch_artwork
            checks again before downloading, but a plain-http URL would else be
            baked into a saved shortcut.
            """
            for key in keys:
                value = game.get(key)
                if isinstance(value, str) and value.startswith("https://"):
                    return value
            return None

        return {
            "capsule": url("capsule_url"),
            # The game's own picture, for when there is no capsule. Some catalogue
            # games have a cover and no capsule, and the empty slot fell back to
            # the gateway's website picture, so many games showed the same blue
            # cloud and looked like duplicates. The frontend composes a capsule
            # from this -- not offered AS the capsule, since these are wide logos
            # and the capsule is a tall portrait.
            "cover": url("cover_url", "banner_url"),
            "header": url("header_url"),
            "hero": url("hero_url", "banner_url"),
            "logo": url("logo_url"),
            # Not a library slot. Steam takes an icon as a file PATH, so this one
            # is downloaded and written to disk, not handed over as bytes -- see
            # applyArtwork. Separate from the logo, which is drawn instead of the
            # title on a game's page (that is why website tiles have no logo but
            # do have this).
            "icon": url("icon_url"),
        }

    def _artwork_proxy(self, game: Dict) -> Dict[str, str]:
        """The same artwork, served back through the gateway.

        Steam applies custom art from bytes, so the plugin fetches the image from
        inside Steam's client -- a cross-origin request. Steam's own CDN sends
        `access-control-allow-origin: *`, but SteamGridDB sends no CORS header, so
        the browser drops the response and cloud-only games kept a blank tile. The
        gateway does send the header, so it can hand over what the CDN will not.
        """
        game_id = game.get("id")
        if not isinstance(game_id, int):
            return {}
        base = self._gateway().rstrip("/")
        return {slot: f"{base}/api/artwork/{game_id}/{slot}"
                for slot in ("capsule", "header", "hero", "logo", "icon")}

    def _to_desired(self, game: Dict) -> List[Dict]:
        """Turn a library entry into the tiles the frontend should make.

        A list, because one game is not always one tile. With sync_all_providers
        on, a game playable on three services becomes three tiles, one per service.
        Off (the default) it is the single preferred route and the list has one
        entry. Every field is checked, not indexed: this is network data, and one
        malformed entry used to raise out of the loop and abandon the whole sync.
        Anything missing what a Steam shortcut needs is skipped and counted.
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
        # Two rules for two kinds of tile: a game is https-only (its URL is
        # written into a shortcut and opened later, so no plain link is stored); a
        # page is checked by weburl, which allows http but refuses anything inside
        # this network -- the same rule the launcher uses, so a shortcut can't be
        # made that then refuses to launch.
        providers = [
            p for p in providers
            # A plain token, because this value has to survive a round trip through
            # a shell-quoted command line and back out of shortcuts.vdf. The reader
            # matches \S+ and would stop at a quote, so a key with a space comes
            # back different -- and a key that no longer matches synced.json makes
            # the sync delete the tile and rebuild it, every time.
            if isinstance(p.get("provider"), str) and p["provider"]
            and _PLAIN_KEY.match(p["provider"])
            and isinstance(p.get("launch_url"), str)
            and (weburl.is_allowed_website_url(p["launch_url"])
                 if p["provider"] in weburl.WEBSITE_PROVIDERS
                 else p["launch_url"].startswith("https://"))
        ]
        if not providers:
            return []

        # A route the subscription cannot reach is not a tile. The gateway marks
        # each route with the tier it still needs. This matters most with a tile
        # per service on, since that mode makes a shortcut of every route: a game
        # free on one service and behind a paywall on another would else get a
        # tile that opens on a "start your free trial" page. Unless dropping them
        # leaves nothing -- a game the user added on purpose keeps one tile even
        # when no route is reachable, and needs_tier says why it asks for money.
        reachable = [p for p in providers if not p.get("needs_tier")]
        if reachable:
            providers = reachable

        chosen = game.get("chosen_provider")
        if self._sync_all_providers():
            # Every route, preferred one first so it keeps whichever tile Steam
            # orders first and the library reads the way it did.
            ordered = sorted(providers, key=lambda p: p["provider"] != chosen)
            # One route per SERVICE, not one per row. The catalog can hold several
            # rows for the same game on the same service (a regional entry, a
            # re-release, a second storefront). One tile per row gives them the
            # same key, so they collide in synced.json and the launch URL flips
            # between them every sync.
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
        # NO URL on the command line. Steam shows launch options verbatim in a
        # game's properties, so a provider deep link would sit there for everyone
        # to read. --game-id resolves a launch instead: the launcher asks the
        # gateway where the game goes now, which keeps a changed URL or provider
        # working without a re-sync. The offline fallback lives in synced.json,
        # read by game id. --provider stays and now also tells the gateway which
        # tile this is -- three tiles for one game share a game id, so without it
        # all three resolve to the same service.
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
                # The game's id on STEAM, carried through so the sync can fill it
                # in on every tile in one call. It was dropped here before, so the
                # frontend's map was always empty and the player badge had nothing
                # to ask Steam about -- the feature was inert.
                "steam_app_id": str(game.get("steam_app_id") or ""),
            })
        return entries

    async def get_website_launch(self) -> Dict[str, Any]:
        """Launch command for a Steam tile that opens the Shelfcloud website.

        Steam's in-client browser forwards no controller input to web pages
        (verified on hardware: zero key events, zero gamepads), so the d-pad is
        dead there. Launching the site as a Steam shortcut instead puts it under
        Steam Input, which can map the d-pad to arrow keys. Uses the same browser
        the user picked for games, so the site and games share one login.
        """
        browser = await asyncio.to_thread(browsers.resolve_browser, self._settings.get("browser"))
        if browser is None:
            # A dict, not None: the frontend reads .ok off every reply, and a bare
            # None reads as "the call broke", not "no browser set yet".
            return {"ok": False, "error": "no browser configured"}

        launcher = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "shelfcloud-launch.py")
        profile = browsers.profile_dir(browser["app_id"])
        # --no-autoplay: our own site has no provider Play button to press.
        options = _shell_join([
            launcher, browser["app_id"], profile, self._gateway(), "--no-autoplay",
        ])
        return {
            "ok": True,
            "exe": "/usr/bin/python3",
            "options": options,
            "name": "Shelfcloud (website)",
            "browser": browser["label"],
        }


    @_sized
    async def fetch_artwork(self, url: str, offset: int = 0) -> Dict[str, Any]:
        """Download one artwork image and return it base64-encoded, in slices.

        `offset` asks for the rest of an image already fetched; the reply says
        `total` so the caller knows when it has all of it (see ARTWORK_CHUNK_CHARS
        for why it can't be one message). The frontend used to fetch these itself,
        which can't work: that code runs inside Steam's client, so every image is a
        cross-origin request and SteamGridDB (no CORS) and our own gateway (mixed
        content) both failed silently. Python has neither limit, so the bytes are
        fetched here and handed over ready to use.
        """
        # https only. These bytes are written to disk and handed to Steam as a
        # shortcut icon, so nothing on the path gets to swap the image. Every
        # artwork URL the catalog serves is already https.
        if not isinstance(url, str) or not url.startswith("https://"):
            return {"ok": False, "error": "unsupported url"}

        # A continuation of the image just sent, answered from memory: asking the
        # CDN again for MB we already hold would turn a fix into a new problem.
        if offset:
            held = getattr(self, "_artwork_part", None)
            if held and held[0] == url:
                return self._artwork_slice(held[1], held[2], offset)
            return {"ok": False, "error": "image no longer held; fetch again"}

        headers = {"User-Agent": ARTWORK_USER_AGENT}
        # Our own gateway needs the credential; a provider's CDN must never see it.
        # The proxy is behind sign-in like every endpoint, and this request used to
        # go out bare -- the one reason that endpoint could not be gated.
        if url.startswith(self._gateway() + "/"):
            token = self._token()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        # On a thread. urlopen blocks, and this runs on the one event loop the
        # whole plugin shares -- so a slow CDN would stall the backend, not just
        # this image: the poll loop, every other RPC, the sync's own progress.
        try:
            content_type, raw = await asyncio.to_thread(
                self._fetch_image, url, headers)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not content_type.startswith("image/"):
            return {"ok": False, "error": f"not an image ({content_type})"}
        if len(raw) > ARTWORK_MAX_BYTES:
            return {"ok": False, "error": "image too large"}

        encoded = base64.b64encode(raw).decode()
        kind = "png" if content_type.endswith("png") else "jpg"
        # Held so the follow-up slices do not re-download the same picture. One
        # entry is enough: the caller finishes an image before the next.
        self._artwork_part = (url, encoded, kind)
        return self._artwork_slice(encoded, kind, 0)

    @staticmethod
    def _artwork_slice(encoded: str, kind: str, offset: int) -> Dict:
        """One message worth of an encoded image, and how much there is."""
        return {
            "ok": True,
            "base64": encoded[offset:offset + ARTWORK_CHUNK_CHARS],
            "kind": kind,
            "offset": offset,
            "total": len(encoded),
        }

    @staticmethod
    def _fetch_image(url: str, headers: Dict[str, str]) -> tuple:
        """Blocking half of fetch_artwork, for asyncio.to_thread.

        Reads one byte over the limit so the caller can tell "at the limit" from
        "too big" without holding the whole thing in memory.
        """
        with _open(url, headers=headers) as response:
            content_type = response.headers.get("Content-Type", "")
            return content_type, response.read(ARTWORK_MAX_BYTES + 1)

    async def clear_library(self) -> Dict[str, Any]:
        """Empty the chosen library. Tiles are removed by the next sync."""
        try:
            result = await asyncio.to_thread(self._request, "/api/me/library/clear", "POST", {})
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @_sized
    async def preview_owned(self) -> Dict[str, Any]:
        """How many owned-on-Steam games could be added in one go."""
        try:
            result = await asyncio.to_thread(self._request, "/api/me/library/owned-preview")
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def add_owned_games(self) -> Dict[str, Any]:
        """Add every game the user can play on cloud.

        No provider argument, so the gateway answers for all at once: games owned
        on Steam, free-to-play ones, and whatever the user's Luna or Xbox Cloud
        tier already covers. Saves picking a few hundred games one at a time on the
        website.
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

        "Add all my games" adds every owned game server-side in one call and then
        makes the tiles one at a time. Cancelling stops the tiles but leaves the
        library holding those games, which would just reappear on the next sync --
        so the cancel undid nothing lasting. Removing them makes cancel mean what
        it says. Only games this run added and never tiled are passed in. Sent in
        batches because the endpoint caps a change at 500 entries.
        """
        # The argument before its contents. This list comes from the frontend, so
        # `for g in None` would be a TypeError out of a Decky callable and show up
        # as a dead panel with nothing to explain it.
        if not isinstance(game_ids, (list, tuple, set)):
            return {"ok": True, "removed": 0}

        # as_id, not a hand-rolled check: an id can arrive as int, float or string,
        # and as_id already rejects bool (a subclass of int that would else become
        # the id 1, a real game).
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
            # Best effort. The tiles are already correct; this just tidies the
            # library behind them, and failing it must not fail the sync.
            decky.logger.warning("Shelfcloud: could not drop %d games: %s",
                                 len(ids), exc)
            return {"ok": False, "error": str(exc), "removed": removed}
        return {"ok": True, "removed": removed}

    async def restore_to_library(self, game_ids: List[int]) -> Dict[str, Any]:
        """Put games back, for a cancelled "remove all".

        That run empties the library server-side in one call and then deletes the
        tiles one at a time. Stopping the deletions alone changes nothing lasting:
        the library is already empty, so every saved tile is no longer wanted and
        the next sync removes it anyway. So the games whose tiles survived are added
        back -- only those, since a game whose tile was already deleted is gone and
        putting it back would undo a deletion the person watched happen.
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

    @_sized
    async def sync_steam_library(self) -> Dict[str, Any]:
        """Re-read which games the user owns from Steam."""
        try:
            result = await asyncio.to_thread(
                self._request, "/api/me/steam-library/refresh", "POST", {}
            )
            return {"ok": result.get("ok", False), **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @_sized
    async def get_desired_library(self, known: str = "") -> Dict[str, Any]:
        """What the Steam library should contain, ready to act on.

        `known` is the version the caller already holds; when nothing has changed
        since, the payload is not sent again. The HTTP fetch from the gateway has
        been conditional for a while, but this reply was not -- the same ~1.6 MB
        crossed Decky's websocket every few minutes just to say nothing changed.
        """
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
                # One bad row must never cost teh user their whole library.
                decky.logger.warning("Skipping a malformed game entry: %s", exc)
                entries = []
            if entries:
                desired.extend(entries)
            else:
                skipped += 1

        if skipped:
            decky.logger.info("Skipped %d entries that were not usable", skipped)

        await self._forget_vanished_tiles()

        # Once per session, off the event loop. Cheap when there is nothing to do
        # (a listdir per Steam user), and it only has work on a device that ran an
        # older build.
        if not self._pruned_layouts:
            self._pruned_layouts = True
            try:
                await asyncio.to_thread(self._prune_stale_layouts)
            except Exception as exc:
                decky.logger.warning("layout prune failed: %s", exc)

        self._last_error = None

        # Versioned over what the caller acts on -- the desired tiles and the
        # tracking record -- not the whole reply, so adding a field later can't
        # quietly stop this from ever matching.
        version = hashlib.sha256(
            json.dumps([desired, self._synced], sort_keys=True,
                       default=str).encode()
        ).hexdigest()[:16]
        if known and known == version:
            # Everything the caller needs, minus the MB it already holds.
            return {"ok": True, "signed_in": True, "unchanged": True,
                    "version": version, "skipped": skipped}
        return {
            "ok": True,
            "signed_in": True,
            "desired": desired,
            "skipped": skipped,
            "tracked": self._synced,
            "version": version,
        }

    def _prune_stale_layouts(self) -> int:
        """Remove layout directories left behind by an older naming rule.

        The name Steam reads was wrong twice before (the shortcut's own case, then
        lower case), and each attempt wrote a directory that nothing cleaned up --
        measured here, 552 dead directories Steam never opened. Kept narrow: a
        directory goes only when all three hold -- its name is one of our shortcut
        names under an old spelling, the name Steam reads today exists beside it
        (so never the only copy), and it sits in a per-user config directory
        (checked by realpath). Returns how many were removed.
        """
        steam_root = os.path.realpath(os.path.expanduser("~/.steam/steam"))
        configs_root = os.path.join(
            steam_root, "steamapps", "common", "Steam Controller Configs")
        if not os.path.isdir(configs_root):
            return 0

        # Every name we have ever written a directory under, and the one we would
        # write now, for each tile we track.
        current, former = set(), set()
        for entry in self._synced.values():
            name = (entry or {}).get("name")
            if not isinstance(name, str) or not name:
                continue
            key = _layout_key(name)
            current.add(key)
            for spelling in (name, name.lower()):
                if spelling != key:
                    former.add(spelling)
        former -= current
        if not former:
            return 0

        removed = 0
        for user_dir in os.listdir(configs_root):
            user_path = os.path.join(configs_root, user_dir, "config")
            if not os.path.isdir(user_path):
                continue
            real_user = os.path.realpath(user_path)
            for name in list(os.listdir(user_path)):
                if name not in former:
                    continue
                key = _layout_key(name)
                # Never the only copy.
                if not os.path.isdir(os.path.join(user_path, key)):
                    continue
                victim = os.path.join(user_path, name)
                if not os.path.realpath(victim).startswith(real_user + os.sep):
                    continue
                try:
                    shutil.rmtree(victim)
                    removed += 1
                except OSError as exc:
                    decky.logger.warning(
                        "could not remove a stale layout directory: %s", exc)
        if removed:
            decky.logger.info(
                "Removed %d controller layout directories left by an older "
                "naming rule", removed)
        return removed

    async def _forget_vanished_tiles(self) -> int:
        """Drop tracked tiles whose Steam shortcut is gone.

        A tile removed from shortcuts.vdf behind our back stays in synced.json and
        every sync treats it as present, so the game drops out of the library for
        good. Forgetting it lets the next sync remake it. Two strikes though, since
        Steam writes that file when it feels like it: a tile made moments ago can be
        missing from the copy on disk, and forgetting it too soon would build a
        duplicate. So it must be absent on two passes, and the suspicion lives in
        memory only, cleared by a restart.
        """
        if not self._synced:
            return 0
        try:
            found = await asyncio.to_thread(shortcuts.shelfcloud_shortcuts)
        except Exception as exc:                      # never fail a sync over it
            decky.logger.warning("could not read shortcuts.vdf: %s", exc)
            return 0
        # An empty read means the file could not be parsed, not that Steam has no
        # shortcuts. forgetting everything then would rebuild the whole library
        # under new app ids and lose every playtime and layout.
        if not found:
            return 0

        real = {entry["app_id"] for entry in found}
        missing = {key for key, entry in self._synced.items()
                   if isinstance(entry, dict) and entry.get("app_id") not in real}

        # Only the ones missing last time too. The rest go on the suspect list and
        # are left alone; anything that came back drops off it.
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
    # Deck does not create or delete them (that wants a real keyboard), it only
    # chooses which one it shows.

    async def get_shelves(self) -> Dict[str, Any]:
        """The account's shelves, and which one this device is on.

        Both come from the gateway, not a local cache: the shelf can be changed
        from the website or another device, and a stale copy would show the wrong
        library with no way for the user to tell.
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

        # `or []` is not enough: it rescues null, but a number or string reaches
        # the loop and raises -- a string iterates one char at a time, an int not
        # at all
        shelf_rows = shelves.get("shelves")
        device_rows = devices.get("devices")
        if not isinstance(shelf_rows, list) or not isinstance(device_rows, list):
            return {"ok": False, "error": "gateway returned an unexpected response"}

        # Match on the id the gateway says is us. With no such id there is no
        # device, and comparing against None would match the first row whose id is
        # also unreadable.
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
        syncs right after -- leaving the old tiles until the next poll would look
        like the setting did not take.
        """
        current = await self.get_shelves()
        if not current.get("ok"):
            return current

        # `is None`, not falsiness: an id of 0 is a device, and treating it as
        # missing would tell a signed-in user to sign in again. device_id has been
        # through as_id(), which makes it safe to put into the path below.
        device_id = current.get("device_id")
        if device_id is None:
            # A token issued before shelves existed has no device attached.
            return {"ok": False, "error": "Sign in again to use shelves on this device"}

        # None is a real choice (the shared library), so an unreadable value must
        # not quietly become it. Anything that is not an id, or an id this account
        # does not have, is refused.
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

        A frontend from before the per-provider split sends a bare game id. It is
        normalised here: with a provider to hand the composite key is built, and
        the old-shaped entry it replaces is dropped so the tile is not recorded
        twice under two keys.
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
                              url: str = "", steam_app_id: str = "") -> Dict:
        """Remember a tile, and where its game launches if the gateway is down.

        `url` is the offline fallback. It used to sit in the Steam shortcut, which
        put a provider deep link in the game's properties for anyone to read; it
        lives here instead, keyed by game and provider, and the launcher reads it
        only when the gateway does not answer. https only, checked here: a bad
        value written now is a bad URL opened later when nothing can correct it.
        """
        key = self._resolve_key(game_id, provider)
        previous = self._synced.get(key) or {}
        keep = url if isinstance(url, str) and url.startswith("https://") else ""
        self._synced[key] = {
            "app_id": app_id, "name": name, "launch": options,
            "art": art, "provider": provider,
            # Keep the previous fallback rather than blanking it when a caller
            # omits one -- an older frontend that does not send `url` yet must not
            # strip the offline route off every tile it touches.
            "url": keep or previous.get("url", ""),
            # The game's id on STEAM, where it has one. Only used to ask Steam the
            # player count: a cloud tile is a non-Steam shortcut, so Steam won't
            # answer that about the tile itself. Kept from the previous record when
            # a caller omits it, same reason as `url`.
            "steam_app_id": (str(steam_app_id) if steam_app_id
                             else previous.get("steam_app_id", "")),
        }
        await self._save_state()
        return {"ok": True}

    @_sized
    async def stray_shortcuts(self) -> Dict:
        """Shelfcloud tiles Steam has that this plugin does not account for.

        Reconcile can only remove what it tracks, so a shortcut created but never
        recorded (a restart between the two, a rolled-back state file) stays
        invisible and the next sync makes a second tile for the same game. That is
        how "Ad Infinitum" ended up on the shelf twice. shortcuts.vdf is the only
        place that knows the truth, and every tile of ours carries `--game-id N` in
        its launch options. Reports, does not repair: removing a shortcut is
        SteamClient's job, and rewriting this file under a running Steam loses
        whatever it flushes next.
        """
        try:
            found = await asyncio.to_thread(shortcuts.shelfcloud_shortcuts)
        except Exception as exc:
            decky.logger.error("could not read shortcuts.vdf: %s", exc)
            return {"ok": False, "error": str(exc), "shortcuts": []}

        # What this plugin believes, so the caller can prefer the tile that already
        # has artwork and playtime over an accidental twin.
        tracked: Dict[str, int] = {}
        for key, entry in self._synced.items():
            app_id = as_id((entry or {}).get("app_id"))
            if app_id is not None:
                tracked[str(key)] = app_id

        # Grouped by game AND provider. Grouped by game alone, the caller deletes
        # every tile in a group but one -- and with sync_all_providers on, a game's
        # tiles on three services are one group, so two would be destroyed every
        # sync with their playtime and layout. Twins are two tiles for the same
        # game on the SAME service, and those are what this is for.
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
                    # Keep the one already tracked; else the oldest, which is
                    # likelier to hold playtime.
                    "keep": tracked[key] if tracked.get(key) in row["app_ids"]
                            else row["app_ids"][0],
                    "tracked": tracked.get(key),
                }
                for key, row in by_route.items()
            ],
        }

    async def tiles_missing_icons(self) -> Dict[str, Any]:
        """App ids of our tiles whose icon file is not on disk, for sync to redo.

        An icon is a path in shortcuts.vdf that Steam reads on every draw, so a
        lost file is a grey circle and nothing ever puts it back: artwork is only
        reapplied when the gateway's art changes. Lost how does not matter here,
        an update's uninstall did it once, this makes any loss heal by itself.
        One listdir, not a stat per tile, the grid folder has thousands of files.
        """
        try:
            present = set(os.listdir(_icon_dir()))
        except OSError:
            return {"ok": False, "app_ids": []}
        missing = set()
        for entry in self._synced.values():
            app_id = entry.get("app_id") if isinstance(entry, dict) else None
            if not isinstance(app_id, int) or isinstance(app_id, bool) or app_id <= 0:
                continue
            if not any(_icon_name(0, app_id, kind) in present for kind in ("png", "jpg")):
                missing.add(app_id)
        return {"ok": True, "app_ids": sorted(missing)}

    async def forget_shortcut(self, game_id: Any) -> Dict:
        key = str(game_id)
        tracked = self._synced.pop(key, None) or {}
        numeric = _key_game_id(key)
        # The tile is gone, so its icon file is dead weight. But only once the LAST
        # tile for this game is gone: with one tile per service they share the
        # game's artwork, so removing one copy must not take the icon another tile
        # is still drawn with.
        if numeric is not None and not any(
                _key_game_id(other) == numeric for other in self._synced):
            self._remove_icon_files(numeric, tracked.get("app_id"))
        await self._save_state()
        return {"ok": True}

    @staticmethod
    def _write_icon(path: str, raw: bytes) -> None:
        """Blocking half of save_icon, for asyncio.to_thread.

        Written whole then moved, so Steam never reads a half-written file -- it
        re-reads this path every time it draws the icon, not once at save time.
        """
        temporary = f"{path}.part"
        with open(temporary, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, path)

    async def save_icon(self, game_id: int, data: str, kind: str,
                        app_id: Optional[int] = None) -> Dict:
        """Write an image to disk and return the path, for SetShortcutIcon.

        Steam takes the four library slots as bytes but takes an icon as a file
        path, so this is the one piece of artwork that must exist as a file. The
        frontend already holds the bytes, so they are passed through rather than
        downloaded again. Named after the SHORTCUT's app id, not the game id:
        Steam names its own library art `<appid>.png` in that same grid folder and
        our game ids start at 1, so `23101.png` would clash with a real Steam app.
        A shortcut app id is a big generated number with the `_icon` suffix every
        other tool uses, so it collides with nothing.
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

        return await self._store_icon(game_id, raw, kind, app_id)

    async def save_icon_from_url(self, game_id: int, url: str,
                                 app_id: Optional[int] = None) -> Dict:
        """Write a shortcut icon from a URL, rather than from handed-over bytes.

        save_icon takes the image as base64, so the frontend pushes the whole
        picture back across Decky's websocket. That was the last big message going
        frontend -> backend and the only one never sliced, and aiohttp closes a
        websocket on a message over 4 MB instead of raising. A closed socket does
        not fail a call, it silences it: the promise never settles, the frontend's
        own timeout can't fire, and the log stops mid-game with no error. Six hunts
        for "the call that hangs" ended here. The bytes never needed to travel --
        this process downloaded the image, so the frontend just names it and the
        file is written from what is already here.
        """
        try:
            game_id = int(game_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad game id"}
        # https only, same as fetch_artwork: written to disk and handed to Steam as
        # a shortcut icon, so nothing on the path gets to swap them.
        if not isinstance(url, str) or not url.startswith("https://"):
            return {"ok": False, "error": "unsupported url"}

        # Usually already here. fetch_artwork holds the last image so its own
        # follow-up slices do not re-download it, and the icon is normally that same
        # image, fetched a moment ago. Going back to the CDN would download it twice.
        held = getattr(self, "_artwork_part", None)
        if held and held[0] == url:
            try:
                raw = base64.b64decode(held[1], validate=True)
            except Exception:
                raw = b""
            if raw:
                return await self._store_icon(game_id, raw, held[2], app_id)

        headers = {"User-Agent": ARTWORK_USER_AGENT}
        if url.startswith(self._gateway() + "/"):
            token = self._token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        try:
            # On a thread: urlopen blocks, and this is the one event loop the whole
            # plugin shares.
            content_type, raw = await asyncio.to_thread(
                self._fetch_image, url, headers)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if not content_type.startswith("image/"):
            return {"ok": False, "error": f"not an image ({content_type})"}
        kind = "png" if content_type.endswith("png") else "jpg"
        return await self._store_icon(game_id, raw, kind, app_id)

    async def _store_icon(self, game_id: int, raw: bytes, kind: str,
                          app_id: Optional[int]) -> Dict:
        """The write both icon paths share, so the naming rule lives once."""
        if kind not in ("png", "jpg"):
            return {"ok": False, "error": f"unsupported kind {kind!r}"}
        if not raw or len(raw) > ARTWORK_MAX_BYTES:
            return {"ok": False, "error": "image missing or too large"}

        path = os.path.join(_icon_dir(), _icon_name(game_id, app_id, kind))
        try:
            # In a thread, like every other write here. The loop answers RPCs --
            # including the panel's "am I signed in" -- so blocking it stalls the
            # whole interface, not just this call.
            await asyncio.to_thread(self._write_icon, path, raw)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}

    def _download_custom_layout(self, name: str) -> Optional[str]:
        """Fetch a custom VDF from the gateway, at most once every ten minutes.

        Cached because install_controller_layout runs for EVERY tile on every sync,
        so a library with 137 tiles asked the gateway for the same unchanged file
        137 times in a row -- about eleven seconds of a sync spent downloading one
        file over and over. Ten minutes, not forever: a layout edited in the admin
        should still reach a device without a restart.
        """
        cached = _LAYOUT_CACHE.get(name)
        if cached and (time.time() - cached[0]) < _LAYOUT_CACHE_TTL:
            return cached[1]
        gateway = self._gateway()
        url = f"{gateway}/api/controller-layout/{urllib.parse.quote(name)}"
        token = self._settings.get("api_token", "")
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": ARTWORK_USER_AGENT,
        }
        # Once the ten minutes are up the only question is "has it changed", and the
        # gateway tags the answer. Asking conditionally turns the recheck from ~17 KB
        # into a couple hundred bytes, which matters on a first sync.
        etag = cached[2] if cached and len(cached) > 2 else None
        if etag:
            headers["If-None-Match"] = etag
        req = urllib.request.Request(url, headers=headers)
        try:
            with _open(url, headers=headers) as resp:
                # Bounded, like _request and fetch_artwork. A controller layout is a
                # few KB of text, and the whole of it is held in memory and a cache.
                raw = resp.read(LAYOUT_MAX_BYTES + 1)
                if len(raw) > LAYOUT_MAX_BYTES:
                    raise ValueError(
                        f"layout {name!r} is larger than {LAYOUT_MAX_BYTES} bytes")
                text = raw.decode("utf-8", errors="replace")
                new_etag = resp.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached:
                # Unchanged. Restart the clock so the next ten minutes are free, not
                # asking again on the very next tile.
                _LAYOUT_CACHE[name] = (time.time(), cached[1], etag)
                return cached[1]
            decky.logger.warning("custom layout download failed: %s", exc)
            return cached[1] if cached else None
        except Exception as exc:
            decky.logger.warning("custom layout download failed: %s", exc)
            # A stale copy beats no layout: a brief gateway outage should not strip
            # a layout off every tile.
            return cached[1] if cached else None
        _LAYOUT_CACHE[name] = (time.time(), text, new_etag)
        return text

    async def layout_selection_base(self) -> Dict:
        """Where the installed layouts live, so tiles can be SELECTED cheaply.

        Installing a layout (a backend call that writes files) and selecting it (a
        pure Steam-client call, no RPC, no disk) are separate costs that were tied
        together. Selecting has to happen for every tile every session, because
        Steam does not select our config on its own -- a registered, on-disk layout
        just sits there unselected. That is why it "worked and then stopped": the
        selection ran only when a tile was created, so a settled library fell back
        to Steam's default. One call per sync returns the base directory and the
        pad's type, and the caller builds a path per tile.
        """
        # Resolved, like install_controller_layout: layouts are written under the
        # real path, and a url through the ~/.steam/steam symlink is a different
        # string for the same file -- the mismatch the layout docs warn about.
        steam_root = os.path.realpath(os.path.expanduser("~/.steam/steam"))
        configs_root = os.path.join(
            steam_root, "steamapps", "common", "Steam Controller Configs")
        if not os.path.isdir(configs_root):
            return {"ok": False, "error": "no controller configs directory"}

        # THE SIGNED-IN ACCOUNT's directory, from its Steam ID. Steam keeps one per
        # account that ever signed in here, and this Deck has four. A config selected
        # under the wrong one does not just fail: Steam clears the selection back to
        # `default://<appid>`, which looks like the layout never applying. Picking
        # the fullest directory was the obvious guess and the wrong one. The account
        # id is the low 32 bits of the Steam ID, exactly what names the directory --
        # arithmetic on something already stored, not a guess about the filesystem.
        steam_id = str(self._settings.get("steam_id64") or "").strip()
        best = None
        if steam_id.isdigit():
            account_id = int(steam_id) - 76561197960265728
            if account_id > 0:
                candidate = os.path.join(configs_root, str(account_id), "config")
                if os.path.isdir(candidate):
                    best = candidate
        if not best:
            # Not signed in, or a directory Steam has not made yet. The most recently
            # touched one is the best remaining sign of who is using the device.
            newest_at = -1.0
            for user_dir in os.listdir(configs_root):
                path = os.path.join(configs_root, user_dir, "config")
                if not os.path.isdir(path):
                    continue
                try:
                    seen = os.path.getmtime(path)
                except OSError:
                    continue
                if seen > newest_at:
                    best, newest_at = path, seen
        if not best:
            return {"ok": False, "error": "no per-user config directory"}
        return {"ok": True, "base": best, "native": "neptune",
                # The revision travels with the flag. The caller records this value
                # when it finishes, so a hard-coded number on that side can't drift
                # from the constant here and keep the repair running every sync.
                "repair": self._settings.get("layout_repair") != LAYOUT_REPAIR,
                "repair_rev": LAYOUT_REPAIR}

    async def install_controller_layout(self, shortcut_name: str,
                                        template_url: str) -> Dict:
        """Copy a Steam Input template into the per-game config directory.

        Non-Steam shortcuts use the SHORTCUT NAME as the directory, not the app id.
        Verified on hardware: the configs on this Deck sit under names like "xbox
        cloud gaming", not numeric ids. Two URL schemes:
          template://  -- a built-in Steam template from controller_base/templates/
          custom://    -- a VDF hosted on the gateway, downloaded to the device
        """
        if not isinstance(shortcut_name, str) or not shortcut_name.strip():
            return {"ok": False, "error": "bad shortcut_name"}
        if not isinstance(template_url, str):
            return {"ok": False, "error": "bad template_url"}
        # The name becomes a DIRECTORY, and it comes from the game's title, which is
        # scraped from each provider's API and not ours to trust. A title with a
        # separator or `..` would let this write files anywhere the plugin can
        # reach. Checked here and again on the resolved path below: this one is easy
        # to read, that one can't be fooled.
        if not _safe_layout_name(shortcut_name):
            decky.logger.warning("refusing a layout directory named %r", shortcut_name)
            return {"ok": False, "error": "unsafe shortcut_name"}

        steam_root = os.path.realpath(os.path.expanduser("~/.steam/steam"))

        # The flavour we pick for a pad we have no layout for. A custom layout has
        # no flavour of its own, and gamepad+mouse fits what ours do: a gamepad that
        # can still reach a cursor, since every one of these services is a web app
        # with a login box somewhere.
        flavour = "gamepad+mouse"

        if template_url.startswith("custom://"):
            vdf_text = await asyncio.to_thread(self._download_custom_layout,
                                               template_url.removeprefix("custom://"))
            if vdf_text is None:
                return {"ok": False, "error": "could not download custom layout"}
        elif template_url.startswith("template://"):
            # basename, because this name comes off the network. The gateway chooses
            # it, and it lands in os.path.join, where both "../../.." and a leading
            # "/" escape the templates directory (join discards everything before an
            # absolute path). That would be an arbitrary read as the `deck` user. The
            # flavour match below runs after the open, so it guards nothing.
            filename = os.path.basename(template_url.removeprefix("template://"))
            source = os.path.join(steam_root, "controller_base", "templates", filename)
            if not os.path.isfile(source):
                return {"ok": False, "error": f"template not found: {filename}"}
            with open(source, "r", encoding="utf-8", errors="replace") as f:
                vdf_text = f.read()
            # controller_neptune_gamepad_fps.vdf -> gamepad_fps, so the other pads
            # get the same intent, not a fixed default.
            named = _re_flavour.match(filename)
            if named:
                flavour = named.group(2)
        else:
            return {"ok": False, "error": "unsupported URL scheme"}

        # Which pad this layout was made for. Ours say neptune: they bind trackpads,
        # gyro and the L4/R4 back buttons, which a PS5 or Xbox pad does not have.
        # That is why the others can't just be this file under a different name --
        # they get Valve's own per-type template instead.
        declared = re.search(r'"controller_type"\s+"controller_([a-z0-9_]+)"', vdf_text)
        native = declared.group(1) if declared else "neptune"

        by_type = {native: vdf_text}
        # Other pad types only when asked for. Writing all seven per game looked free
        # and was not: on this Deck it became 24,813 files and 436 MB, and Steam
        # indexes that folder at startup, so the panel never finished loading. Almost
        # all of it is for hardware nobody plugged in. The Deck's own pad is the one
        # certainty and is the default; the rest are a setting for PS5/Xbox players.
        if self._settings.get("extra_pad_layouts"):
            for other in CONTROLLER_TYPES:
                if other in by_type:
                    continue
                path = _best_template(steam_root, other, flavour)
                if not path:
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        by_type[other] = f.read()
                except (OSError, ValueError):
                    # ValueError as well as OSError: a path with a NUL raises
                    # ValueError from open(), not an OSError, so it used to slip past
                    # this guard and take the whole layout install with it.
                    pass

        # Rewrite the url field to point to the real destination. Custom VDFs arrive
        # with whatever url the author set, built-in templates have none.
        def _placeholder(text: str) -> Optional[str]:
            """Put a rewritable url in, or say we could not.

            The insertion used to match the literal '"controller_mappings"\n{',
            which was only ever tried on our own layouts and now runs over Valve's
            template for every pad type. One different byte of whitespace and the
            replace silently does nothing, so the file lands with no autosave path
            and Steam treats it as read-only -- a layout that does not apply looks
            just like one that does not exist. So: match loosely, and when even that
            fails say so and skip the type rather than write a dead file.
            """
            text = re.sub(r'"url"\s+"[^"]*"', '"url"\t\t"__PLACEHOLDER__"', text)
            if "__PLACEHOLDER__" in text:
                return text
            text, inserted = re.subn(
                r'("controller_mappings"\s*\{)',
                '\\1\n\t"url"\t\t"__PLACEHOLDER__"\n\t"export_type"\t\t"template"',
                text, count=1)
            return text if inserted else None

        prepared = {}
        for ctype, text in by_type.items():
            ready = _placeholder(text)
            if ready is None:
                decky.logger.warning(
                    "controller layout for %s has no place to put an autosave "
                    "url; skipping that pad rather than writing a read-only "
                    "config", ctype)
                continue
            prepared[ctype] = ready
        by_type = prepared



        # Off the event loop, always. This is plain synchronous file work that used
        # to sit in an async method. One game is a few files; a full re-sync is
        # thousands, and this coroutine never yielded for the whole of it. Decky's
        # RPC runs on the same loop, so the panel just showed "Loading..." until the
        # sync finished.
        return await asyncio.to_thread(
            self._write_layouts, steam_root, shortcut_name, by_type, native)

    def _write_layouts(self, steam_root: str, shortcut_name: str,
                       by_type: Dict[str, str], native: str) -> Dict:
        """Put each controller type's layout on disk. Runs in a thread."""
        installed = 0
        autosave_url = None
        configs_root = os.path.join(
            steam_root, "steamapps", "common", "Steam Controller Configs")
        for user_dir in os.listdir(configs_root):
            user_path = os.path.join(configs_root, user_dir, "config")
            if not os.path.isdir(user_path):
                continue
            dest_dir = os.path.join(user_path, _layout_key(shortcut_name))
            # Belt and braces: resolve it and confirm it really is inside the
            # per-user config directory. The check above catches the obvious shapes;
            # this one catches the rest and is the one that has to be right.
            if not os.path.realpath(dest_dir).startswith(
                    os.path.realpath(user_path) + os.sep):
                decky.logger.warning(
                    "layout directory for %r resolves outside %s; skipping",
                    shortcut_name, user_path)
                continue
            os.makedirs(dest_dir, exist_ok=True)
            written = 0
            for ctype, text in by_type.items():
                dest = os.path.join(dest_dir, f"controller_{ctype}.vdf")
                try:
                    final = text.replace("__PLACEHOLDER__", f"autosave://{dest}")
                    # The Deck's own pad is the one the caller cares about (it is
                    # what SetSelectedConfigForApp is pointed at), so only that one is
                    # reported back. The rest are laid down for whatever gets plugged
                    # in later.
                    if ctype == native:
                        autosave_url = f"autosave://{dest}"
                    # Only write when it would change something. This runs for every
                    # tile on every sync across every Steam user directory, so on this
                    # Deck it was 548 rewrites of files that already said exactly this
                    # -- and now one per controller type on top. That is why the read
                    # stays.
                    try:
                        with open(dest, "r", encoding="utf-8", errors="replace") as f:
                            unchanged = f.read() == final
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        with open(dest, "w", encoding="utf-8") as f:
                            f.write(final)
                        decky.logger.info("controller layout: wrote %s", dest)
                    _register_configset(user_path, _layout_key(shortcut_name), ctype)
                    # Counted here, after the write and the registration that can
                    # both raise. Counted on the way in before, so a layout that
                    # failed to land still reported itself as installed.
                    written += 1
                except OSError as exc:
                    decky.logger.warning("controller layout copy failed: %s", exc)
            # One per DIRECTORY, which is what this number has always meant. Counting
            # files instead would turn a four-user Deck from "4" into "28" for the
            # same work.
            if written:
                installed += 1
        return {"ok": installed > 0, "installed": installed,
                "autosave_url": autosave_url if installed else None}

    async def log_note(self, message: str) -> Dict[str, Any]:
        """Put a line from the frontend into the plugin's log.

        The sync runs in Steam's browser context, where console.warn goes to a CEF
        console nobody reads -- least of all afterwards, when the question is "what
        was it doing when it stopped". This log is a file on disk, the only record
        that survives. Kept dull: a capped string and nothing else, since it is
        called from code whose inputs we do not control.
        """
        if not isinstance(message, str):
            return {"ok": False}
        decky.logger.warning("frontend: %s", message[:300])
        return {"ok": True}

    # How long a player count may be reused. A badge on a game page is opened over
    # and over on a Deck, so the cache is the diffrence between one request per game
    # and one per press of the A button. Fifteen minutes because this number is
    # context, not a readout -- nobody cares that a game went from 61,000 to 60,400.
    PLAYERS_CACHE_SECONDS = 900.0

    # A failure is remembered for much less time than a count. Both shared the number
    # above before, so a minute of bad connectivity left a dash on that game for the
    # full fifteen. A count is stable enough to reuse; a failure is about right now.
    # Still a cache, not zero: an appid Steam refuses (delisted, region-locked) must
    # not be re-asked on every page open.
    PLAYERS_FAILURE_CACHE_SECONDS = 60.0
    # How old a good count may be to stand in for a failed ask. Older is not
    # "playing right now" anymore.
    PLAYERS_STALE_SECONDS = 2 * 3600.0
    PLAYERS_URL = ("https://api.steampowered.com/ISteamUserStats/"
                   "GetNumberOfCurrentPlayers/v1/?appid=%s")

    async def clear_player_cache(self) -> Dict[str, Any]:
        """The Clear cache button in Settings. Counts and fallbacks both go, so
        the next page open asks Steam and nothing old can come back."""
        self._players_cache = {}
        self._players_good = {}
        decky.logger.info("Player count cache cleared")
        return {"ok": True}

    async def current_players(self, steam_app_id: str) -> Dict[str, Any]:
        """How many people are playing this game on Steam right now.

        The same public endpoint the PlayCount plugin uses -- no key, no account,
        answers for any appid. This exists because Steam won't answer it for our
        tiles: a cloud game is a non-Steam shortcut, so the client has no appid to
        ask about, even though the game itself is usually on Steam. Returns ok=False,
        not a zero, when there is nothing to say -- "not on Steam" and "nobody
        playing" are different facts.
        """
        if not isinstance(steam_app_id, str) or not steam_app_id.isdigit():
            return {"ok": False, "error": "not on steam"}

        now = time.time()
        cached = self._players_cache.get(steam_app_id)
        if cached:
            age = now - cached[0]
            keep = (self.PLAYERS_CACHE_SECONDS if cached[1].get("ok")
                    else self.PLAYERS_FAILURE_CACHE_SECONDS)
            if age < keep:
                return cached[1]

        try:
            payload = await asyncio.to_thread(self._fetch_players, steam_app_id)
        except Exception as exc:
            # logged, the badge only shows a dash and that says nothing about why
            decky.logger.warning("player count for %s failed: %s", steam_app_id, exc)
            # Remembered too, else an appid Steam refuses would be re-asked on every
            # page open for ever.
            payload = {"ok": False, "error": str(exc)}

        if payload.get("ok"):
            self._players_good[steam_app_id] = (now, payload)
        else:
            payload = self._recent_good_count(steam_app_id, now) or payload
        self._players_cache[steam_app_id] = (now, payload)
        return payload

    def _recent_good_count(self, steam_app_id: str, now: float) -> Optional[Dict[str, Any]]:
        """The last good count for this game if it is recent, marked stale.

        A failed ask (a blip at Steam, the Deck just awake) used to show a dash
        while the number from a moment ago was right there. Too old and it is no
        longer "playing right now", then the dash is the honest answer."""
        kept = self._players_good.get(steam_app_id)
        if not kept or now - kept[0] > self.PLAYERS_STALE_SECONDS:
            return None
        return {**kept[1], "stale": True}

    @classmethod
    def _fetch_players(cls, steam_app_id: str) -> Dict[str, Any]:
        """Blocking half of current_players, for asyncio.to_thread."""
        with _open(cls.PLAYERS_URL % steam_app_id,
                   headers={"User-Agent": ARTWORK_USER_AGENT}) as response:
            raw = response.read(64 * 1024)
        body = json.loads(raw.decode("utf-8", errors="replace"))
        inner = body.get("response") or {}
        # result 1 is Steam's "this appid is fine". Anything else means it has no
        # answer, which is not the same as nobody playing.
        if inner.get("result") != 1 or "player_count" not in inner:
            return {"ok": False, "error": "no player count for this app"}
        return {"ok": True, "players": int(inner["player_count"])}

    async def set_steam_ids(self, ids: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in the Steam appid for tiles that already exist.

        The appid is written by record_shortcut, but a sync only records a tile that
        CHANGED, so on a settled library nothing is recorded and the field stayed
        empty on every tile -- so the player badge had nothing to ask Steam about.
        Bumping ART_REVISION would reach every tile but re-apply all artwork to
        backfill one string. This is the same reach for one call: the sync already
        holds every desired game, so it sends the whole map once and leaves the tiles
        it does not mention alone.
        """
        if not isinstance(ids, dict):
            return {"ok": False, "error": "bad ids"}

        changed = 0
        for key, steam_app_id in ids.items():
            entry = self._synced.get(str(key))
            if not isinstance(entry, dict):
                continue
            value = str(steam_app_id or "")
            if entry.get("steam_app_id", "") == value:
                continue
            entry["steam_app_id"] = value
            changed += 1

        if changed:
            await self._save_state()
        return {"ok": True, "updated": changed}

    @_sized
    async def tracked_providers(self) -> Dict[str, Any]:
        """app id -> which service that tile launches, for the library badge.

        Its own call, not a whole-library read: the badge runs on a patched Steam
        route once per game page opened, and asking the gateway for hundreds of games
        to learn one word would be silly. This reads only what is in memory. Keys are
        strings because they cross to JavaScript, where an object key is a string
        anyway -- returning ints would just make the frontend convert them back.
        """
        out: Dict[str, str] = {}
        steam_ids: Dict[str, str] = {}
        game_ids: Dict[str, str] = {}
        for key, entry in self._synced.items():
            if not isinstance(entry, dict):
                continue
            app_id = entry.get("app_id")
            provider = entry.get("provider")
            if isinstance(app_id, int) and isinstance(provider, str) and provider:
                out[str(app_id)] = provider
                # the catalog id in front of the key, "2" for 2:boosteroid. same for
                # every service the game is on, so a tile page can offer the others
                game_id = str(key).split(":", 1)[0]
                if game_id.isdigit():
                    game_ids[str(app_id)] = game_id
                # Absent for a game not on Steam, and for tiles recorded before this
                # was kept. Absent means "no player count for this one", not a zero.
                steam = entry.get("steam_app_id")
                if steam:
                    steam_ids[str(app_id)] = str(steam)
        return {"ok": True, "providers": out, "steam_ids": steam_ids, "game_ids": game_ids}

    async def sync_finished(self, added: int, removed: int, updated: int) -> Dict:
        self._last_sync = asyncio.get_event_loop().time()
        decky.logger.info("Sync: +%d -%d ~%d (tracked=%d)",
                          added, removed, updated, len(self._synced))
        return {"ok": True}

    # ---------------------------------------------------------------- status

    def _provider_counts(self) -> Dict[str, int]:
        """How many synced tiles belong to each provider, for the panel chips.

        Tiles recorded before providers were tracked have none. They are counted as
        unknown, not assumed to be Boosteroid: that guess was fine while Boosteroid
        was the only provider and is wrong now a tile could be Xbox Cloud. The next
        sync fills it in.
        """
        counts: Dict[str, int] = {}
        for entry in self._synced.values():
            provider = entry.get("provider") or "unknown"
            counts[provider] = counts.get(provider, 0) + 1
        return counts

    def _detected_display(self) -> str:
        """What the panel shows under the scale dropdown.

        The same answer the launcher acts on, from the same code, so the panel and
        the launch log can't disagree, and "Auto" is not a number the user has to
        trust. Cached, because this shells out to xrandr and the panel polls status
        every 1.5 seconds while a sync runs -- uncached, a long sync would spawn
        hundreds of processes to answer a question that does not change. A display is
        docked in seconds, not milliseconds. The launcher does NOT share this cache:
        it probes once per launch, where the answer has to be current.
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

        Off unless turned on. It multiplies the library (measured here, 387 tiles
        become 636), and most people want the one route their preferred service
        gives, so this is a choice someone makes on purpose.
        """
        return self._settings.get("sync_all_providers") is True

    def _sync_minutes(self) -> int:
        """Minutes between automatic syncs, clamped to a sane range. 0 is a real
        value here - it means the auto-sync is turned off."""
        try:
            minutes = int(self._settings.get("sync_minutes", DEFAULT_SYNC_MINUTES))
        except (TypeError, ValueError):
            return DEFAULT_SYNC_MINUTES
        return max(0, min(minutes, 24 * 60))

    async def _verify_auth(self) -> bool:
        """Is the stored token still accepted? Re-checked at most once a minute.

        Without this the panel trusted a string on disk, so a revoked or expired
        token looked like a working session: every button enabled, every one failing.
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
            # Gateway unreachable is not "signed out" -- do not throw away a good
            # credential because the network blipped.
            pass
        return self._auth_ok

    @staticmethod
    def _version() -> str:
        return decky.DECKY_PLUGIN_VERSION or "unknown"

    def _play_menu_on(self) -> bool:
        """On unless switched off. An old settings file has no key, that is on."""
        return self._settings.get("play_menu_enabled") is not False

    @_sized
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
            # On unless turned off: a badge saying where a tile streams from is the
            # whole point of the tiles looking different.
            "badge_enabled": self._settings.get("badge_enabled") is not False,
            # The mark on the highlighted card in the home carousel and grid. Its own
            # switch, not shared with the game page: two places, and wanting one
            # without the other is normal.
            "badge_on_cards": self._settings.get("badge_on_cards") is not False,
            "players_enabled": bool(self._settings.get("players_enabled")),
            # our services in the dropdown beside Play on a Steam game. on unless
            # switched off, like the badge
            "play_menu_enabled": self._play_menu_on(),
            "players_position": (self._settings.get("players_position")
                                 if self._settings.get("players_position")
                                 in self.BADGE_POSITIONS else "tr"),
            "badge_position": (self._settings.get("badge_position")
                               if self._settings.get("badge_position")
                               in self.BADGE_POSITIONS else "bl"),
            "last_error": self._last_error,
            # Only while a sign-in is in flight; the browser that asks for this
            # closed the panel that first showed it.
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

        Returns immediately. Progress is read from get_status(), so the panel can be
        closed and reopened mid-download without losing it. Refuses anything not on
        the offer list: `app_id` comes from the frontend and ends up in an argv, and
        "the caller is our own UI" is not worth relying on when the check is one line.
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
        LD_LIBRARY_PATH to find them. Fine for Python, but it poisons any subprocess
        that links the system OpenSSL -- flatpak needs the system version and crashes
        with "OPENSSL_3.5.0 not found" when it picks up Decky's older copy.
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

    # Keys the frontend may write. Anything else, api_token above all, is rejected.
    # Shelf selection goes through set_shelf, not here. A new panel dropdown must be
    # listed here.
    ALLOWED_SETTINGS = {"browser", "sync_minutes", "sync_all_providers", "ui_scale",
                        "badge_enabled", "badge_on_cards", "badge_position",
                        "extra_pad_layouts", "players_enabled",
                        "players_position", "play_menu_enabled",
                        # Not a preference: marks that the one-off layout re-selection
                        # has been done on this device. See LAYOUT_REPAIR.
                        "layout_repair"}

    # Where the provider badge sits on a game's page. Checked against a set, not
    # trusted: this value is written into a style, and a dropdown is not the only
    # thing that can call set_setting.
    BADGE_POSITIONS = ("tl", "tm", "tr", "bl", "bm", "br")

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        if key not in self.ALLOWED_SETTINGS:
            decky.logger.warning("Rejected setting key: %s", key)
            return await self.get_status()
        # badge_position is written into a CSS rule on a patched Steam page, so it is
        # checked against the list, not trusted -- set_setting is an RPC and the
        # dropdown is not the only thing that can call it.
        if key == "badge_position" and value not in self.BADGE_POSITIONS:
            decky.logger.warning("Rejected badge position: %r", value)
            return await self.get_status()
        # a switch, so only a real bool. the string "false" would read as on
        if key == "play_menu_enabled" and not isinstance(value, bool):
            decky.logger.warning("Rejected play menu value: %r", value)
            return await self.get_status()
        self._settings[key] = value
        await self._save_settings()
        return await self.get_status()

    # ------------------------------------------------------------ poll loop

    async def _poll_loop(self):
        """Ask the gateway for changes periodically and nudge the frontend.

        Python cannot make shortcuts -- only the frontend can reach SteamClient -- so
        this emits an event rather than acting directly.
        """
        # Let Steam settle, then a random extra wait so a fleet that updated together
        # does not poll together. Without it every copy rolled out in one release
        # starts within seconds of the others and stays in lockstep.
        base = max(MIN_SYNC_SECONDS, self._sync_minutes() * 60)
        spread = random.uniform(0, min(base, STARTUP_SPREAD_MAX_SECONDS))
        await asyncio.sleep(STARTUP_SETTLE_SECONDS + spread)

        while True:
            # Read the interval each tick, not once, so changing the setting takes
            # effect on the next tick without a plugin reload. 0 is "off": skip the
            # sync but keep looping, so turning it back on resumes on its own.
            minutes = self._sync_minutes()
            try:
                if self._token() and minutes > 0:
                    await decky.emit(SYNC_EVENT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                decky.logger.warning("Poll failed: %s", exc)

            await asyncio.sleep(
                MIN_SYNC_SECONDS if minutes <= 0
                else next_poll_delay(max(MIN_SYNC_SECONDS, minutes * 60))
            )
