"""Shelfcloud — Decky plugin backend.

Talks to the Shelfcloud gateway, caches the catalog on disk, and detects which
browser can be used to launch cloud games. Adding shortcuts to Steam happens on
the frontend, because only there is Steam's client API reachable.

Stdlib only: Decky's Python environment does not guarantee third-party packages.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

import decky

import browsers

DEFAULT_GATEWAY = "http://127.0.0.1:8000"
CATALOG_PATH = "/api/catalog"
REQUEST_TIMEOUT = 20


def _settings_file() -> str:
    directory = decky.DECKY_PLUGIN_SETTINGS_DIR
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "settings.json")


def _cache_file() -> str:
    directory = decky.DECKY_PLUGIN_RUNTIME_DIR
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "catalog.json")


class Plugin:
    def __init__(self):
        self._settings: Dict[str, Any] = {}

    # ------------------------------------------------------------- lifecycle

    async def _main(self):
        self._settings = await asyncio.to_thread(self._read_settings)
        decky.logger.info("Shelfcloud loaded (gateway=%s)", self._gateway())

    async def _unload(self):
        decky.logger.info("Shelfcloud unloaded")

    # -------------------------------------------------------------- settings

    def _read_settings(self) -> Dict[str, Any]:
        try:
            with open(_settings_file(), encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_settings(self, settings: Dict[str, Any]) -> None:
        with open(_settings_file(), "w", encoding="utf-8") as handle:
            json.dump(settings, handle, indent=2)

    def _gateway(self) -> str:
        return (self._settings.get("gateway_url") or DEFAULT_GATEWAY).rstrip("/")

    async def get_settings(self) -> Dict[str, Any]:
        return {
            "gateway_url": self._gateway(),
            "browser": self._settings.get("browser"),
        }

    async def set_setting(self, key: str, value: Any) -> Dict[str, Any]:
        self._settings[key] = value
        await asyncio.to_thread(self._write_settings, self._settings)
        return await self.get_settings()

    # --------------------------------------------------------------- catalog

    def _read_cache(self) -> Dict[str, Any]:
        try:
            with open(_cache_file(), encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cache(self, etag: Optional[str], games: List[Dict]) -> None:
        with open(_cache_file(), "w", encoding="utf-8") as handle:
            json.dump({"etag": etag, "games": games}, handle)

    def _fetch_catalog(self, force: bool) -> Dict[str, Any]:
        """Fetch the catalog, revalidating with ETag so repeats are cheap."""
        cached = {} if force else self._read_cache()
        request = urllib.request.Request(self._gateway() + CATALOG_PATH)
        request.add_header("Accept-Encoding", "identity")  # keep decoding simple
        if cached.get("etag"):
            request.add_header("If-None-Match", cached["etag"])

        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                games = json.loads(response.read().decode())
                etag = response.headers.get("ETag")
                self._write_cache(etag, games)
                return {"ok": True, "games": games, "cached": False}
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached.get("games"):
                return {"ok": True, "games": cached["games"], "cached": True}
            return self._fallback(cached, f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return self._fallback(cached, str(exc))

    @staticmethod
    def _fallback(cached: Dict[str, Any], error: str) -> Dict[str, Any]:
        """A dead gateway must not mean an empty library — serve the cache."""
        if cached.get("games"):
            return {"ok": True, "games": cached["games"], "cached": True, "error": error}
        return {"ok": False, "games": [], "error": error}

    async def get_catalog(self, force: bool = False) -> Dict[str, Any]:
        result = await asyncio.to_thread(self._fetch_catalog, force)
        if not result.get("ok"):
            decky.logger.warning("Catalog fetch failed: %s", result.get("error"))
        return result

    # -------------------------------------------------------------- browsers

    async def get_browsers(self) -> List[Dict[str, str]]:
        """Installed, supported browsers. Empty means the user must install one."""
        return await asyncio.to_thread(browsers.detect_browsers)

    async def build_launch(self, url: str) -> Optional[Dict[str, str]]:
        """The exe + launch options that open one cloud game.

        Built here rather than on the frontend because only the backend knows
        which browsers are installed and where their profiles live.
        """
        browser = await asyncio.to_thread(browsers.resolve_browser, self._settings.get("browser"))
        if browser is None:
            return None
        launch = await asyncio.to_thread(browsers.build_launch, browser["app_id"], url)
        return {**launch, "browser": browser["app_id"], "browser_label": browser["label"]}
