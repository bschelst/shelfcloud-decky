// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// Calls into the plugin's Python backend (main.py).

import { callable } from "@decky/api";

import type {
  AddOwnedResult, LibraryResponse, LoginPoll, LoginStart, OwnedPreview,
  SteamLibraryResult, Status,
} from "./types";

export const getStatus = callable<[], Status>("get_status");
export const setSetting = callable<[key: string, value: unknown], Status>("set_setting");

/** Start installing one of the offered browsers. Returns as soon as it has
 *  started; progress is read from `status.install`, so closing the panel
 *  mid-download does not lose it. */
export const installBrowser =
  callable<[app_id: string], { ok: boolean; error?: string }>("install_browser");
/** Clear a finished install's message so the panel can move on. */
export const dismissInstall = callable<[], Status>("dismiss_install");

export const startLogin = callable<[], LoginStart>("start_login");
export const pollLogin = callable<[session_token: string], LoginPoll>("poll_login");
export const logout = callable<[], { ok: boolean }>("logout");

export const clearLibrary = callable<[], { ok: boolean; removed?: number; error?: string }>("clear_library");
export const previewOwned = callable<[], OwnedPreview>("preview_owned");
export const addOwnedGames = callable<[], AddOwnedResult>("add_owned_games");
export const syncSteamLibrary = callable<[], SteamLibraryResult>("sync_steam_library");
export const getDesiredLibrary = callable<[], LibraryResponse>("get_desired_library");
/** Artwork is downloaded by the Python backend, not the browser: inside Steam's
 *  client every image URL is cross-origin, and our own gateway is plain HTTP
 *  (mixed content). Python has neither restriction. */
export const fetchArtwork =
  callable<[url: string], { ok: boolean; base64?: string; kind?: string; error?: string }>("fetch_artwork");
/** Write an icon to disk and get its path back.
 *
 *  Steam takes the four library slots as bytes, but takes a shortcut icon as a
 *  filesystem PATH — it reads that file every time it draws the icon. So this
 *  one slot has to become a real file, which only the Python side can do. */
export const saveIcon =
  callable<[game_id: number, data: string, kind: string], { ok: boolean; path?: string; error?: string }>("save_icon");
/** The account's shelves, and which one this device is pointed at.
 *
 *  Asked of the gateway each time rather than cached: the shelf can be changed
 *  from the website or another device, and a stale copy would show the wrong
 *  library with nothing to reveal the mismatch. */
export const getShelves =
  callable<[], { ok: boolean; shelves?: Array<{ id: number; name: string; games: number }>;
                 device_id?: number | null; shelf_id?: number | null;
                 device_name?: string | null; error?: string }>("get_shelves");

/** Point this device at a shelf, or at null for the shared games. */
export const setShelf =
  callable<[shelf_id: number | null], { ok: boolean; shelf_id?: number | null; error?: string }>("set_shelf");

export const recordShortcut =
  callable<[game_id: number, app_id: number, name: string, options: string, art: string,
           provider: string, url: string], unknown>("record_shortcut");
export const forgetShortcut = callable<[game_id: number], unknown>("forget_shortcut");

/** Every Shelfcloud tile Steam actually has, read from shortcuts.vdf.
 *
 *  The plugin's own record can drift: a shortcut created but never written
 *  down is invisible to reconcile, and the next sync then makes a SECOND tile
 *  for the same game. This is the file's answer rather than the plugin's. */
export const strayShortcuts = callable<[], {
  ok: boolean;
  error?: string;
  shortcuts: Array<{
    game_id: number;
    app_ids: number[];
    /** The one to keep: the tracked tile if there is one, else the oldest. */
    keep: number;
    tracked?: number | null;
  }>;
}>("stray_shortcuts");

export const syncFinished =
  callable<[added: number, removed: number, updated: number], unknown>("sync_finished");
export const applyOverrides =
  callable<[], { ok: boolean; browser?: string; error?: string }>("apply_overrides");
