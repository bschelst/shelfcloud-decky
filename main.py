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
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import decky

import browsers

DEFAULT_GATEWAY = "http://127.0.0.1:8000"
REQUEST_TIMEOUT = 20
SYNC_EVENT = "shelfcloud/library"

# How often to check the gateway for library changes, in minutes.
SYNC_INTERVAL_CHOICES = [1, 5, 10, 30, 60]
DEFAULT_SYNC_MINUTES = 5
# Re-read between ticks rather than caching, so a changed setting takes effect
# on the next tick instead of requiring a plugin reload.
MIN_SYNC_SECONDS = 30


def _settings_file() -> str:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")


def _state_file() -> str:
    os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "synced.json")


class Plugin:
    def __init__(self):
        self._settings: Dict[str, Any] = {}
        # game_id -> {"app_id": int, "name": str, "launch": str}
        # Without this, a game removed on the web could never be found and
        # removed here, and re-syncing would create duplicate tiles.
        self._synced: Dict[str, Dict[str, Any]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._last_error: Optional[str] = None
        self._last_sync: Optional[float] = None

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
        return (self._settings.get("gateway_url") or DEFAULT_GATEWAY).rstrip("/")

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

        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = response.read().decode()
            return json.loads(payload) if payload else None

    # ------------------------------------------------------------------ auth

    async def start_login(self) -> Dict[str, Any]:
        """Begin sign-in. The frontend opens login_url via steam://openurl,
        because Game Mode has no browser of its own."""
        try:
            result = await asyncio.to_thread(
                self._request, "/auth/session?web=0", "POST", None, False
            )
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def poll_login(self, session_token: str) -> Dict[str, Any]:
        """Check whether the user finished signing in on the web."""
        try:
            result = await asyncio.to_thread(
                self._request, f"/auth/session/{session_token}", "GET", None, False
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        if result.get("status") != "ok":
            return {"ok": True, "status": "pending"}

        self._settings["api_token"] = result["api_token"]
        self._settings["steam_id64"] = result.get("steam_id64")
        await self._save_settings()
        return {"ok": True, "status": "signed_in", "steam_id64": result.get("steam_id64")}

    async def logout(self) -> Dict[str, Any]:
        try:
            await asyncio.to_thread(self._request, "/api/me/logout", "POST", {})
        except Exception:
            pass  # local sign-out must work even when the gateway is unreachable
        self._settings.pop("api_token", None)
        self._settings.pop("steam_id64", None)
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
        return {
            "capsule": game.get("capsule_url"),
            "header": game.get("header_url"),
            "hero": game.get("hero_url") or game.get("banner_url"),
            "logo": game.get("logo_url"),
        }

    def _to_desired(self, game: Dict) -> Optional[Dict]:
        """Turn a library entry into everything the frontend needs to make a tile."""
        providers = game.get("providers") or []
        if not providers:
            return None

        chosen = game.get("chosen_provider")
        provider = next((p for p in providers if p["provider"] == chosen), providers[0])

        browser = browsers.resolve_browser(self._settings.get("browser"))
        if browser is None:
            return None

        launcher = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "shelfcloud-launch.py")
        profile = browsers.profile_dir(browser["app_id"])
        options = " ".join([
            launcher, browser["app_id"], profile, provider["launch_url"],
        ])

        return {
            "game_id": game["id"],
            "name": game["name"],
            "provider": provider["provider"],
            "exe": "/usr/bin/python3",
            "options": options,
            "artwork": self._artwork(game),
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
        options = " ".join([
            launcher, browser["app_id"], profile, self._gateway(), "--no-autoplay",
        ])
        return {
            "exe": "/usr/bin/python3",
            "options": options,
            "name": "Shelfcloud (website)",
            "browser": browser["label"],
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

        desired = []
        skipped = 0
        for game in result.get("games", []):
            if game.get("missing"):
                skipped += 1
                continue
            entry = self._to_desired(game)
            if entry:
                desired.append(entry)
            else:
                skipped += 1

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
        await self._save_state()
        return {"ok": True}

    async def sync_finished(self, added: int, removed: int, updated: int) -> Dict:
        self._last_sync = asyncio.get_event_loop().time()
        decky.logger.info("Sync: +%d -%d ~%d (tracked=%d)",
                          added, removed, updated, len(self._synced))
        return {"ok": True}

    # ---------------------------------------------------------------- status

    def _provider_counts(self) -> Dict[str, int]:
        """How many synced tiles belong to each provider, for the panel chips."""
        counts: Dict[str, int] = {}
        for entry in self._synced.values():
            provider = entry.get("provider") or "boosteroid"
            counts[provider] = counts.get(provider, 0) + 1
        return counts

    def _sync_minutes(self) -> int:
        """Minutes between automatic syncs, clamped to a sane range."""
        try:
            minutes = int(self._settings.get("sync_minutes", DEFAULT_SYNC_MINUTES))
        except (TypeError, ValueError):
            return DEFAULT_SYNC_MINUTES
        return max(1, min(minutes, 24 * 60))

    async def get_status(self) -> Dict[str, Any]:
        browser = await asyncio.to_thread(browsers.resolve_browser, self._settings.get("browser"))
        return {
            "signed_in": bool(self._token()),
            "steam_id64": self._settings.get("steam_id64"),
            "gateway_url": self._gateway(),
            "tracked": len(self._synced),
            "by_provider": self._provider_counts(),
            "browser": browser,
            "browsers": await asyncio.to_thread(browsers.detect_browsers),
            "sync_minutes": self._sync_minutes(),
            "sync_choices": SYNC_INTERVAL_CHOICES,
            "last_error": self._last_error,
        }

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
        # Let Steam settle before the first sync; adding shortcuts while the
        # library is still loading is asking for trouble.
        await asyncio.sleep(20)
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
            await asyncio.sleep(max(MIN_SYNC_SECONDS, self._sync_minutes() * 60))
