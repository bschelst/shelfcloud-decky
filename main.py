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
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import decky

import browsers


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

# Artwork is fetched here rather than in the browser; see fetch_artwork.
ARTWORK_USER_AGENT = "Shelfcloud/0.1 (+https://github.com/bschelst/shelfcloud)"
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


class Plugin:
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        # game_id -> {"app_id": int, "name": str, "launch": str}
        # Without this, a game removed on the web could never be found and
        # removed here, and re-syncing would create duplicate tiles.
        self._synced: Dict[str, Dict[str, Any]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._login_task: Optional[asyncio.Task] = None
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
        self._synced = await asyncio.to_thread(self._read_json, _state_file())
        decky.logger.info("Shelfcloud ready (gateway=%s, tracked=%d)",
                          self._gateway(), len(self._synced))
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _unload(self):
        if self._poll_task:
            self._poll_task.cancel()
        if self._login_task:
            self._login_task.cancel()
        decky.logger.info("Shelfcloud unloaded")

    # ---------------------------------------------------------------- config

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
        request.add_header("Accept", "application/json")
        if data:
            request.add_header("Content-Type", "application/json")
        if authenticated:
            token = self._token()
            if not token:
                raise PermissionError("not signed in")
            request.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                payload = response.read().decode()
                self._auth_ok = True
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            # 401 is not a transient error. The token was revoked, expired, or
            # the user signed out elsewhere — keeping it would leave the panel
            # claiming to be signed in while every action failed.
            if exc.code == 401 and authenticated:
                self._forget_credentials()
                raise PermissionError("signed out — sign in again") from exc
            raise

    # ------------------------------------------------------------------ auth

    async def start_login(self) -> Dict[str, Any]:
        """Begin sign-in. The frontend opens login_url via steam://openurl,
        because Game Mode has no browser of its own."""
        try:
            result = await asyncio.to_thread(
                self._request, "/auth/session?web=0", "POST", None, False
            )
            # Wait for completion here, not in the panel: opening Steam's
            # browser closes the panel and would take the waiter with it.
            if self._login_task and not self._login_task.done():
                self._login_task.cancel()
            self._login_task = asyncio.create_task(
                self._await_login(result["session_token"]))
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

            self._settings["api_token"] = result["api_token"]
            self._settings["steam_id64"] = result.get("steam_id64")
            # A fresh credential invalidates any cached "not signed in" answer.
            self._auth_ok = True
            self._auth_checked = 0.0
            await self._save_settings()
            decky.logger.info("Signed in as %s", result.get("steam_id64"))
            # Populate the library straight away rather than waiting a tick.
            await decky.emit(SYNC_EVENT)
            return

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
            """First value that is actually a usable URL, else None."""
            for key in keys:
                value = game.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
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
            if isinstance(p.get("launch_url"), str) and p["launch_url"].startswith(("http://", "https://"))
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
        # The launcher needs to know the provider, not just the URL: what a
        # signed-out page looks like and where the Play control lives differ per
        # provider, and Xbox serves the page in the browser's language.
        # The URL is passed as a fallback; --game-id lets the launcher ask the
        # gateway where the game goes right now. That keeps a changed URL or a
        # changed provider preference working without a re-sync, and lets the
        # gateway count launches — while a sleeping gateway still leaves the
        # game playable.
        options = _shell_join([
            launcher, browser["app_id"], profile, provider["launch_url"],
            "--game-id", str(game_id),
            "--provider", provider["provider"],
        ])

        return {
            "game_id": game_id,
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
        if not url or not url.startswith(("http://", "https://")):
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
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
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

    # ------------------------------------------------- shortcut bookkeeping

    async def record_shortcut(self, game_id: int, app_id: int, name: str,
                              options: str, art: str = "", provider: str = "") -> Dict:
        self._synced[str(game_id)] = {
            "app_id": app_id, "name": name, "launch": options,
            "art": art, "provider": provider,
        }
        await self._save_state()
        return {"ok": True}

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

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
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
