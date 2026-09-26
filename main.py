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
import functools
import json
import os
import random
import re
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

# path -> (etag, raw json text). See _request.
#
# The gateway tags every library answer, and the library is polled on a timer
# while changing hardly at all -- so without this the same few hundred
# kilobytes came down again on every sync to say nothing had happened. The
# RAW TEXT is kept rather than the parsed object: handing the same dict back
# to two callers means the second one sees whatever the first did to it, and
# re-parsing is still far cheaper than the transfer it replaces.
_ETAG_CACHE: Dict[str, Any] = {}
_ETAG_CACHE_MAX = 8

# Must match backend/providers.py LAYOUT_GAMEPAD and LAYOUT_WEB_BROWSER.
_LAYOUT_GAMEPAD = "template://controller_neptune_gamepad_fps.vdf"
_LAYOUT_WEB_BROWSER = "template://controller_neptune_webbrowser.vdf"


# Controller types we lay a config down for besides the one the layout was
# authored against.
#
# The point is that plugging a pad in just works. A config for a controller
# nobody owns costs a few KB and is ignored; a MISSING config means Steam
# falls back to its own default and the service's shortcuts are unreachable,
# which is the state every non-Deck pad was in.
# neptune is FIRST and deliberately included rather than assumed.
#
# The authored layout supplies its own type and the rest fall back to Valve's
# templates -- so listing only the others quietly relied on every layout we
# ship being a Deck one. A custom VDF exported from an Xbox pad declares
# controller_xbox360, and then the one controller this plugin exists for is
# the only one that gets no config at all. Cheap to include, and the loop
# skips it anyway whenever it IS the authored type.
CONTROLLER_TYPES = ("neptune", "ps5", "ps4", "xbox360", "xboxone",
                    "switch_pro", "generic")

# controller_neptune_gamepad_fps.vdf -> ("neptune", "gamepad_fps"). Anchored on
# the known type names because several of them contain an underscore too, so
# splitting on the first or last one gets switch_pro wrong either way.
_re_flavour = re.compile(
    r"^controller_(neptune|ps3|ps4|ps5|xbox360|xboxone|switch_pro|generic|android|apple)"
    r"_(.+)\.vdf$")

# What to fall back to when a type has no template for the flavour we wanted.
# Not every type ships every flavour: ps4/ps5 have gamepad+mouse, xbox360 and
# xboxone do not, and only neptune has webbrowser at all. Ordered best first.
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

    A title is not a path, but Steam treats it as one: the configset keys each
    entry by the shortcut's NAME, and the directory is that name joined onto
    the per-user config directory. So a title containing a slash NESTS, and
    Steam resolves it back the same way -- "Disney/Pixar WALL-E (Amazon Luna)"
    is a real game whose layout lives two directories deep, and Steam finds it
    there quite happily.

    This used to refuse any separator at all. That was an over-correction:
    guarding against a title climbing out of the directory is right, but a
    plain slash is not climbing, and two Luna games silently lost their
    layout every sync because of it.

    What is refused is a segment that means something to the filesystem rather
    than naming a folder:

      ".."          climbs, and a title has no reason to
      ""            a leading or doubled slash -- and join() on an absolute
                    path DISCARDS everything before it, so "/etc/x" would not
                    be inside the config directory at all
      "." only      resolves to the directory itself, so the layout would be
                    written over whatever shares that name

    The resolved-path check at the destination is still there and is still the
    one that has to be right; this only makes the refusal legible and early.
    """
    if not shortcut_name or not shortcut_name.strip():
        return False
    parts = shortcut_name.split(os.sep)
    if os.altsep:
        parts = [bit for part in parts for bit in part.split(os.altsep)]
    return all(part not in ("", ".", "..") for part in parts)


def _sized(fn):
    """Log a reply big enough to close Decky's websocket.

    aiohttp's WebSocketResponse refuses a message over 4 MB by CLOSING the
    connection -- no exception on either side, so the plugin does not fail, it
    stops, and every later call waits for an answer that cannot arrive. A 7 MB
    Steam hero did that on every sync until artwork was cut into slices, and
    something else is still doing it at around the eightieth call.

    Guessing which reply took seven attempts last time. This names it.
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


# One connection pool for every call this plugin makes.
#
# urlopen opens a NEW TCP connection -- and a TLS handshake with it -- for
# every single request. A sync makes hundreds: a layout per new tile, six
# artwork slots per changed game, a slice per two megabytes of a hero, a
# player count per page opened. All to the same two or three hosts, each one
# paying for a fresh handshake before a byte of payload moves, and each one
# leaving a socket in TIME_WAIT behind it.
#
# urllib3 keeps them alive and hands the same connection back. It is already
# present -- Decky ships it -- so this costs no new dependency.
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
            # A handful of live connections is plenty: the calls are issued a
            # few at a time by design, and a bigger pool would only hold
            # sockets open for work that is not coming.
            maxsize=4,
            block=False,
            # Redirects yes, retries no. A CDN moving an image is normal; an
            # automatic retry is not ours to decide -- the callers already
            # treat a failure as "skip this one", and a silent second attempt
            # would double a slow request's cost without telling anybody.
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


def _open(url: str, headers: Optional[Dict[str, str]] = None, method: str = "GET",
          body: Optional[bytes] = None, timeout: Optional[float] = None):
    """A pooled request that FAILS the way urlopen does.

    Raising HTTPError on a non-2xx is the point. Every caller here already
    handles that shape -- a 304 meaning "what you hold is current", a 401
    meaning "sign in again" -- and rewriting all of it around urllib3's
    status-code-in-the-response would be a second change riding on a first.
    """
    if _urllib3 is None:                # pragma: no cover
        request = urllib.request.Request(url, data=body, method=method,
                                         headers=headers or {})
        return urllib.request.urlopen(request, timeout=timeout or REQUEST_TIMEOUT,
                                      context=SSL_CTX)

    try:
            # NEVER None. urllib3 reads an explicit timeout=None as "wait for
        # ever" and it OVERRIDES the pool's default -- so every call without
        # an explicit one lost its deadline the moment this was pooled. A
        # stalled read then held a thread-pool worker for good, and after
        # four of those every asyncio.to_thread queued behind them and the
        # backend answered nothing at all: get_status, tracked_providers,
        # the lot. The socket was open the whole time, which is what made it
        # look like a transport problem for hours.
        raw = _http().request(
            method, url, body=body, headers=headers or {},
            preload_content=False,
            timeout=_urllib3.Timeout(total=timeout or REQUEST_TIMEOUT),
        )
    except _urllib3.exceptions.HTTPError as exc:
        # Translated, not passed through. urllib3 raises MaxRetryError and
        # SSLError where urlopen raised URLError, and the callers here are
        # written against urlopen -- _request's handler that names a
        # certificate failure in the log catches URLError, so an untranslated
        # urllib3 error would sail past it and the one diagnostic that says
        # "your TLS is wrong" would never be printed again.
        #
        # Wrapped rather than reraised bare so the original is still attached
        # for anything that looks.
        raise urllib.error.URLError(exc) from exc
    if not (200 <= raw.status < 300):
        raw.read()                      # drain, so the connection can be reused
        raw.release_conn()
        raise urllib.error.HTTPError(url, raw.status, str(raw.reason),
                                     raw.headers, None)
    return _PooledResponse(raw)


def _register_configset(config_dir: str, app_key: str,
                        controller_type: str = "neptune") -> None:
    """Register a per-game config in the configset file so Steam finds it.

    Without this entry Steam ignores the VDF file in the per-game directory.
    boosteroid-steamos does the same thing in _update_configset.

    One configset per controller type, and it has to match the config it is
    registering: writing controller_ps5.vdf but only ever touching
    configset_controller_neptune.vdf leaves the PS5 config on disk and unused,
    which looks exactly like the layout not working.
    """
    configset = os.path.join(config_dir,
                             f"configset_controller_{controller_type}.vdf")
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

# The most base64 one RPC reply may carry.
#
# This is the bug that stopped every sync today, and it is not about size in
# the abstract -- it is aiohttp's. Decky carries RPC replies over a websocket,
# and aiohttp's WebSocketResponse refuses a message over 4 MB by CLOSING the
# connection. No error reaches either side: the socket simply goes, and every
# call after it waits for an answer that can never come. That is why a sync
# died at call 475 one run and call 77 the next -- the number never mattered,
# the first oversized reply did.
#
# Steam hero art is 6-7 MB on this Deck, which is 9 MB of base64 in one
# message. So an image is sent in slices now and the frontend puts it back
# together. Two megabytes leaves room for the JSON around it and still moves a
# 7 MB hero in five messages.
# See docs/RPC-PAYLOAD-LIMITS.md. Decky's websocket closes on a message over
# 4 MB without raising, so nothing large crosses it whole -- artwork is sliced
# to this, and the icon is named by URL rather than shipped as bytes.
ARTWORK_CHUNK_CHARS = 192 * 1024

# Two megabytes was picked against aiohttp's 4 MB ceiling -- one message under
# the limit, as few messages as possible. That reasoning was about a single
# message and the freezes are not: every one of them followed a BURST, five
# replies of 1-2 MB inside one second, after which the backend answered
# nothing at all while its socket stayed open and its process stayed idle.
#
# So the number is chosen for the burst instead. A 7 MB hero is 52 small
# messages rather than 5 enormous ones -- more round trips over a local
# socket, which costs almost nothing, in exchange for never handing the
# router megabytes faster than it can move them.

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
        self._players_cache: Dict[str, Any] = {}
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

        # Only a plain GET is safe to revalidate: a POST has a side effect and
        # must never be answered from anything held here. Keyed on the full
        # path so ?shelf=... is a different entry, because it is a different
        # answer.
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
                if conditional:
                    etag = response.headers.get("ETag")
                    if etag:
                        # Bounded, and cleared wholesale rather than evicted
                        # one by one: there are a handful of GET paths, so if
                        # this is ever full something is generating keys and
                        # the useful response is to stop holding any of them.
                        if (len(_ETAG_CACHE) >= _ETAG_CACHE_MAX
                                and path not in _ETAG_CACHE):
                            _ETAG_CACHE.clear()
                        _ETAG_CACHE[path] = (etag, payload)
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            # 304 is the gateway agreeing that what we already hold is current.
            # urllib raises for it because it is not a 2xx, so it arrives here
            # rather than above.
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

    def _clear_session_state(self) -> None:
        """Drop everything tied to the account that was signed in.

        There are two ways to stop being signed in -- the gateway rejecting
        the token, and the user pressing Sign out -- and each used to clear
        this by hand. They drifted the moment anything was added: the ETag
        cache went into the involuntary path only, so a DELIBERATE sign-out
        left the previous account's library sitting in memory, which is the
        one case where somebody has actually asked for it to be gone.

        One place, so the next thing that belongs here cannot miss a path.
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
            pass   # in-memory state is already correct; disk catches up later

    async def logout(self) -> Dict[str, Any]:
        try:
            await asyncio.to_thread(self._request, "/api/me/logout", "POST", {})
        except Exception:
            pass  # local sign-out must work even when the gateway is unreachable
        if self._login_task and not self._login_task.done():
            self._login_task.cancel()
        self._clear_session_state()
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
            # The game's own picture, for when there is no capsule.
            #
            # 79 catalogue games have a cover and no capsule — Amazon Luna's
            # smaller titles and a few Boosteroid demos. Steam's capsule slot
            # then stayed empty and the tile fell back to the gateway's
            # website picture, so eighteen games in one library were the same
            # blue cloud and a search for any of them looked like duplicates.
            # The frontend draws a capsule from this instead. Not offered AS
            # the capsule: these are wide logos, and Steam's capsule is a tall
            # portrait, so it has to be composed rather than stretched.
            "cover": url("cover_url", "banner_url"),
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

        # A route the subscription cannot reach is not a tile.
        #
        # The gateway marks each route with the rung it still needs. Dropping
        # those matters most with a tile per service on, because that mode
        # makes a shortcut out of EVERY route: Sonic Frontiers is free on
        # Boosteroid and "Luna Premium" on Amazon, and a Luna Prime account got
        # both tiles — the Luna one opening on "Included in Luna Premium, start
        # your 7-day free trial". Ranking could not fix that; ranking chooses
        # between routes and this mode keeps all of them.
        #
        # Unless that leaves nothing. A game reachable on no route at all is
        # still a game the user deliberately added, and silently having no tile
        # looks like the sync lost it. It keeps one, and the gateway's
        # needs_tier says why it will ask for money.
        reachable = [p for p in providers if not p.get("needs_tier")]
        if reachable:
            providers = reachable

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


    @_sized
    async def fetch_artwork(self, url: str, offset: int = 0) -> Dict[str, Any]:
        """Download one artwork image and return it base64-encoded, in slices.

        `offset` asks for the rest of an image already fetched. The reply says
        `total`, so the caller knows when it has all of it -- see
        ARTWORK_CHUNK_CHARS for why this cannot be one message.

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

        # A continuation of the image just sent. Answered from memory: asking
        # the CDN again for six megabytes we already hold, four more times, is
        # the sort of thing that turns a fix into a different problem.
        if offset:
            held = getattr(self, "_artwork_part", None)
            if held and held[0] == url:
                return self._artwork_slice(held[1], held[2], offset)
            return {"ok": False, "error": "image no longer held; fetch again"}

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

        encoded = base64.b64encode(raw).decode()
        kind = "png" if content_type.endswith("png") else "jpg"
        # Held so the follow-up slices do not re-download the same picture.
        # One entry is enough: the caller asks for a whole image before it
        # moves to the next.
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

        Reads one byte more than the limit so the caller can tell "exactly at
        the limit" from "too big" without holding the whole thing in memory.
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
                              url: str = "", steam_app_id: str = "") -> Dict:
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
            # The game's id on STEAM, where it has one -- 83% of the catalogue
            # does. Only used to ask Steam how many people are playing it: a
            # cloud tile is a non-Steam shortcut, so Steam will not answer that
            # about the tile itself.
            #
            # Kept from the previous record when a caller omits it, for the
            # same reason as `url` -- an older frontend must not quietly strip
            # it off every tile it touches.
            "steam_app_id": (str(steam_app_id) if steam_app_id
                             else previous.get("steam_app_id", "")),
        }
        await self._save_state()
        return {"ok": True}

    @_sized
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

    @staticmethod
    def _write_icon(path: str, raw: bytes) -> None:
        """Blocking half of save_icon, for asyncio.to_thread.

        Written whole then moved, so Steam can never read a half-written file
        -- it re-reads this path every time it draws the icon, not once at
        save time.
        """
        temporary = f"{path}.part"
        with open(temporary, "wb") as handle:
            handle.write(raw)
        os.replace(temporary, path)

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

        return await self._store_icon(game_id, raw, kind, app_id)

    async def save_icon_from_url(self, game_id: int, url: str,
                                 app_id: Optional[int] = None) -> Dict:
        """Write a shortcut icon from a URL, rather than from handed-over bytes.

        save_icon takes the image as base64, which means the frontend pushes
        the whole picture back across Decky's websocket. That was the last big
        message going FRONTEND -> BACKEND and the only one never sliced, while
        the same image travelling the other way has been chunked at
        ARTWORK_CHUNK_CHARS for exactly this reason: aiohttp CLOSES a websocket
        on a message over 4 MB instead of raising.

        A closed socket does not fail a call, it SILENCES it. The promise never
        settles, so the frontend's own timeout cannot fire either -- the log
        stops mid-game with no error on either side. Six separate hunts for
        "the call that hangs" ended here.

        And it was reached on ordinary games. The icon falls back to the
        CAPSULE for anything shipping no icon of its own, and a capsule is
        600x900; measured on hardware, the sync died on the FIRST game of every
        run having logged every step before this one.

        The bytes never needed to travel. This process DOWNLOADED that image --
        it is what served the slices -- so the frontend names it instead, in a
        few hundred bytes, and the file is written from what is already here.
        """
        try:
            game_id = int(game_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "bad game id"}
        # https only, same as fetch_artwork: these bytes are written to disk
        # and handed to Steam as a shortcut icon, so nothing on the path gets
        # to swap them.
        if not isinstance(url, str) or not url.startswith("https://"):
            return {"ok": False, "error": "unsupported url"}

        # Usually already here. fetch_artwork holds the last image so its own
        # follow-up slices do not re-download it, and the icon is normally that
        # very image, collected slice by slice a moment ago. Going back to the
        # CDN would be a second download of the same picture for every game.
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
            # On a thread: urlopen blocks, and this is the one event loop the
            # whole plugin shares.
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
            # In a thread, like every other write here. This one was left on
            # the event loop, and the loop is what answers RPCs -- including
            # the one the panel uses to ask whether it is signed in. Blocking
            # it stalls the plugin's whole interface, not just this call.
            await asyncio.to_thread(self._write_icon, path, raw)
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
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": ARTWORK_USER_AGENT,
        }
        # Once the ten minutes are up the question is only "has it changed",
        # and the gateway has tagged the answer since it started serving these.
        # Asking conditionally turns the recheck from seventeen kilobytes into
        # a couple of hundred bytes, which matters on a first sync where every
        # new tile can reach this.
        etag = cached[2] if cached and len(cached) > 2 else None
        if etag:
            headers["If-None-Match"] = etag
        req = urllib.request.Request(url, headers=headers)
        try:
            with _open(url, headers=headers) as resp:
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
                new_etag = resp.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached:
                # Unchanged. Restart the clock so the next ten minutes are free
                # rather than asking again on the very next tile.
                _LAYOUT_CACHE[name] = (time.time(), cached[1], etag)
                return cached[1]
            decky.logger.warning("custom layout download failed: %s", exc)
            return cached[1] if cached else None
        except Exception as exc:
            decky.logger.warning("custom layout download failed: %s", exc)
            # A stale copy beats no layout at all: the gateway being briefly
            # unreachable should not strip a layout off every tile.
            return cached[1] if cached else None
        _LAYOUT_CACHE[name] = (time.time(), text, new_etag)
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
        if not _safe_layout_name(shortcut_name):
            decky.logger.warning("refusing a layout directory named %r", shortcut_name)
            return {"ok": False, "error": "unsafe shortcut_name"}

        steam_root = os.path.realpath(os.path.expanduser("~/.steam/steam"))

        # The flavour we would pick for a pad we have no authored layout for.
        # A custom layout has no flavour of its own, and gamepad+mouse is the
        # honest translation of what ours do: a gamepad that can still reach a
        # cursor, because every one of these services is a web app with a
        # login box somewhere.
        flavour = "gamepad+mouse"

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
            # controller_neptune_gamepad_fps.vdf -> gamepad_fps, so the other
            # pads get the same INTENT rather than a fixed default.
            named = _re_flavour.match(filename)
            if named:
                flavour = named.group(2)
        else:
            return {"ok": False, "error": "unsupported URL scheme"}

        # Which pad this layout was actually authored for. Ours say neptune:
        # they bind trackpads, gyro and the L4/R4 back buttons, none of which
        # a PS5 or Xbox pad has. That is why the others cannot simply be this
        # file under a different name -- the bindings would reference inputs
        # that do not exist -- and get Valve's own per-type template instead.
        declared = re.search(r'"controller_type"\s+"controller_([a-z0-9_]+)"', vdf_text)
        native = declared.group(1) if declared else "neptune"

        by_type = {native: vdf_text}
        # Other pad types only when asked for.
        #
        # Writing all seven for every game looked free and was not: seven
        # files per game per Steam user directory is twenty-eight, and on this
        # Deck that became 24,813 files and 436 MB. Steam indexes that folder
        # at startup, and the sync that produced it blocked long enough that
        # the panel never finished loading.
        #
        # Almost all of it is for hardware nobody has attached. The Deck's own
        # controller is the one certainty, so that is the default, and the
        # rest are a setting for people who actually play on a PS5 or Xbox pad.
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
                except OSError:
                    pass

        # Rewrite the url field to point to the actual destination. Custom VDFs
        # arrive with whatever url the author set (often a path from a different
        # project), and built-in templates have none at all.
        def _placeholder(text: str) -> Optional[str]:
            """Put a rewritable url in, or say we could not.

            Custom layouts arrive with whatever url their author saved; Valve's
            built-in templates have none at all and need one inserted.

            The insertion used to match the literal '"controller_mappings"\n{'.
            That was only ever tried against layouts we authored ourselves, and
            it is now run over Valve's template for every pad type -- files
            nobody here has read. A single different byte of whitespace and the
            replace silently does nothing: no placeholder, so nothing to
            substitute, so the file lands with no autosave path and Steam
            treats it as read-only. A layout that quietly does not apply is
            indistinguishable from one that does not exist.

            So: match tolerantly, and when even that fails say so and skip the
            type rather than write a file that cannot work.
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



        # Off the event loop, always.
        #
        # This is plain synchronous file work -- a read and a write per layout,
        # plus a read and a rewrite of the configset it belongs to -- and it
        # sat directly in an async method. One game is a handful of files and
        # nobody noticed; a full re-sync is thousands, and for the whole of it
        # this coroutine never yielded. Decky's RPC runs on the same loop, so
        # the panel asking "are you signed in" simply waited, and the plugin
        # showed "Loading..." until the sync finished.
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
            written = 0
            for ctype, text in by_type.items():
                dest = os.path.join(dest_dir, f"controller_{ctype}.vdf")
                try:
                    final = text.replace("__PLACEHOLDER__", f"autosave://{dest}")
                    # The Deck's own pad is the one the caller cares about --
                    # it is what SetSelectedConfigForApp gets pointed at -- so
                    # only that one is reported back. The rest are laid down
                    # for whatever gets plugged in later, and nothing selects
                    # them up front.
                    if ctype == native:
                        autosave_url = f"autosave://{dest}"
                    # Only write when it would change something. This runs for
                    # every tile on every sync across every Steam user
                    # directory, so on this Deck that was 137 tiles x 4
                    # directories = 548 rewrites of files that already said
                    # exactly this -- and it is now one per controller type on
                    # top of that, which is precisely why the read stays.
                    try:
                        with open(dest, "r", encoding="utf-8", errors="replace") as f:
                            unchanged = f.read() == final
                    except OSError:
                        unchanged = False
                    if not unchanged:
                        with open(dest, "w", encoding="utf-8") as f:
                            f.write(final)
                        decky.logger.info("controller layout: wrote %s", dest)
                    _register_configset(user_path, shortcut_name, ctype)
                    # Counted HERE, after the write and the registration that
                    # can both raise. It used to be counted on the way in, so
                    # a layout that failed to land still reported itself as
                    # installed -- and that is now up to seven files per
                    # directory rather than one, so seven chances to say a
                    # thing happened that did not.
                    written += 1
                except OSError as exc:
                    decky.logger.warning("controller layout copy failed: %s", exc)
            # One per DIRECTORY, which is what this number has always meant.
            # Counting files instead would turn a normal four-user Deck from
            # "4" into "28" for the same work and make the figure unreadable.
            if written:
                installed += 1
        return {"ok": installed > 0, "installed": installed,
                "autosave_url": autosave_url if installed else None}

    async def log_note(self, message: str) -> Dict[str, Any]:
        """Put a line from the frontend into the plugin's log.

        The sync runs in Steam's browser context, where console.warn goes to a
        CEF console nobody is looking at -- least of all afterwards, when the
        question is "what was it doing when it stopped". This log is a file on
        disk, which is the only record that survives the thing it describes.

        Deliberately dull: a capped string and nothing else. It is called from
        code the plugin does not control the inputs of.
        """
        if not isinstance(message, str):
            return {"ok": False}
        decky.logger.warning("frontend: %s", message[:300])
        return {"ok": True}

    # How long a player count may be reused, and how long a failure is
    # remembered. Steam's own store refreshes this on the order of minutes,
    # and a badge on a game page is opened over and over on a Deck -- so a
    # short cache is the difference between one request per game and one per
    # press of the A button.
    PLAYERS_CACHE_SECONDS = 300.0
    PLAYERS_URL = ("https://api.steampowered.com/ISteamUserStats/"
                   "GetNumberOfCurrentPlayers/v1/?appid=%s")

    async def current_players(self, steam_app_id: str) -> Dict[str, Any]:
        """How many people are playing this game on Steam right now.

        The same public endpoint the PlayCount plugin uses. No key, no
        account: it answers for any appid.

        This exists because Steam will not answer it for OUR tiles. A cloud
        game is a non-Steam shortcut, so the client has no appid to ask about
        and every player-count plugin shows nothing on those pages -- even
        though the game itself is usually on Steam and the number is public.

        Returns ok=False rather than a zero when there is nothing to say. A
        game that is not on Steam and a game nobody is playing are different
        facts, and showing "0 playing" for the first is simply wrong.
        """
        if not isinstance(steam_app_id, str) or not steam_app_id.isdigit():
            return {"ok": False, "error": "not on steam"}

        now = time.time()
        cached = self._players_cache.get(steam_app_id)
        if cached and (now - cached[0]) < self.PLAYERS_CACHE_SECONDS:
            return cached[1]

        try:
            payload = await asyncio.to_thread(self._fetch_players, steam_app_id)
        except Exception as exc:
            # Remembered too. An appid Steam refuses -- delisted, region-locked
            # -- would otherwise be re-asked on every page open, for ever.
            result = {"ok": False, "error": str(exc)}
            self._players_cache[steam_app_id] = (now, result)
            return result

        self._players_cache[steam_app_id] = (now, payload)
        return payload

    @classmethod
    def _fetch_players(cls, steam_app_id: str) -> Dict[str, Any]:
        """Blocking half of current_players, for asyncio.to_thread."""
        with _open(cls.PLAYERS_URL % steam_app_id,
                   headers={"User-Agent": ARTWORK_USER_AGENT}) as response:
            raw = response.read(64 * 1024)
        body = json.loads(raw.decode("utf-8", errors="replace"))
        inner = body.get("response") or {}
        # result 1 is Steam's own "this appid is fine". Anything else means it
        # has no answer, which is not the same as nobody playing.
        if inner.get("result") != 1 or "player_count" not in inner:
            return {"ok": False, "error": "no player count for this app"}
        return {"ok": True, "players": int(inner["player_count"])}

    async def set_steam_ids(self, ids: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in the Steam appid for tiles that already exist.

        The appid is written by record_shortcut -- but a sync only records a
        tile that CHANGED, so on a settled library nothing is recorded and the
        field stayed empty on all 890 tiles. The player badge then had nothing
        to ask Steam about and never drew, which is the feature being inert by
        construction rather than merely unfinished.

        Bumping ART_REVISION would have reached every tile, at the price of
        re-applying artwork across the whole library to backfill one string.
        This is the same reach for a single call: the sync already holds every
        desired game, so it sends the whole map once and the tiles it does not
        mention are left alone.
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

        Its own call rather than reading the whole library: the badge runs on
        a patched Steam route, once per game page opened, and asking the
        gateway for several hundred games to learn one word would be absurd.
        This reads only what is already in memory.

        Keys are strings because they cross to JavaScript, where a number that
        large is fine but an object key is a string regardless — returning
        ints would make the frontend convert them back on every lookup.
        """
        out: Dict[str, str] = {}
        steam_ids: Dict[str, str] = {}
        for entry in self._synced.values():
            if not isinstance(entry, dict):
                continue
            app_id = entry.get("app_id")
            provider = entry.get("provider")
            if isinstance(app_id, int) and isinstance(provider, str) and provider:
                out[str(app_id)] = provider
                # Absent for the one game in six that is not on Steam, and for
                # tiles recorded before this was kept. Absent means "no player
                # count for this one", which is the honest answer rather than
                # a zero.
                steam = entry.get("steam_app_id")
                if steam:
                    steam_ids[str(app_id)] = str(steam)
        return {"ok": True, "providers": out, "steam_ids": steam_ids}

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
            # On unless turned off: a badge that says where a tile streams
            # from is the point of having the tiles look different at all.
            "badge_enabled": self._settings.get("badge_enabled") is not False,
            # The mark on the highlighted card in the home carousel and the
            # grid. Its own switch rather than sharing one with the game page:
            # they are two places, and wanting one without the other is a
            # perfectly ordinary thing to want.
            "badge_on_cards": self._settings.get("badge_on_cards") is not False,
            "players_enabled": bool(self._settings.get("players_enabled")),
            "players_position": (self._settings.get("players_position")
                                 if self._settings.get("players_position")
                                 in self.BADGE_POSITIONS else "tr"),
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
                        "badge_enabled", "badge_on_cards", "badge_position",
                        "extra_pad_layouts", "players_enabled",
                        "players_position"}

    # Where the provider badge sits on a game's page. Validated as a set
    # rather than trusted: this value is written into a style, and a dropdown
    # is not the only thing that can call set_setting.
    BADGE_POSITIONS = ("tl", "tm", "tr", "bl", "bm", "br")

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
