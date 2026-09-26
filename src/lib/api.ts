// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Calls into the plugin's Python backend (main.py).

import { guarded } from "./rpc";

import type {
  AddOwnedResult, LibraryResponse, LoginPoll, LoginStart, OwnedPreview,
  SteamLibraryResult, Status,
} from "./types";

/** Put a line in the plugin's log file.
 *
 *  The sync runs where console output goes to a CEF console nobody reads, and
 *  the interesting question is always asked after the fact. */
export const logNote = guarded<[message: string], { ok: boolean }>("log_note");

export const getStatus = guarded<[], Status>("get_status");
export const setSetting = guarded<[key: string, value: unknown], Status>("set_setting");

/** Start installing one of the offered browsers. Returns as soon as it has
 *  started; progress is read from `status.install`, so closing the panel
 *  mid-download does not lose it. */
export const installBrowser =
  guarded<[app_id: string], { ok: boolean; error?: string }>("install_browser");
/** Clear a finished install's message so the panel can move on. */
export const dismissInstall = guarded<[], Status>("dismiss_install");

export const startLogin = guarded<[], LoginStart>("start_login");
export const pollLogin = guarded<[session_token: string], LoginPoll>("poll_login");
export const logout = guarded<[], { ok: boolean }>("logout");

export const clearLibrary = guarded<[], { ok: boolean; removed?: number; error?: string }>("clear_library");
export const previewOwned = guarded<[], OwnedPreview>("preview_owned");
export const addOwnedGames = guarded<[], AddOwnedResult>("add_owned_games");
/** Take games back out of the library, for a cancelled bulk add.
 *
 *  Cancel means "stop, and forget the rest": the bulk add put every owned game
 *  in the library in one call, so stopping the tiles alone left them all there
 *  to appear on the next sync. Only games that run added and never tiled are
 *  ever passed here. */
export const dropFromLibrary =
  guarded<[game_ids: number[]], { ok: boolean; removed?: number; error?: string }>(
    "drop_from_library");
/** Put games back, for a cancelled "remove all".
 *
 *  That run empties the library server-side first, so stopping the tile
 *  deletions alone changes nothing that lasts — the survivors are no longer
 *  wanted and the next sync takes them. Only tiles that still exist are
 *  restored. */
export const restoreToLibrary =
  guarded<[game_ids: number[]], { ok: boolean; restored?: number; error?: string }>(
    "restore_to_library");
export const syncSteamLibrary = guarded<[], SteamLibraryResult>("sync_steam_library");
export const getDesiredLibrary = guarded<[], LibraryResponse>("get_desired_library");
/** Artwork is downloaded by the Python backend, not the browser: inside Steam's
 *  client every image URL is cross-origin, and our own gateway is plain HTTP
 *  (mixed content). Python has neither restriction. */
export const fetchArtwork =
  guarded<[url: string, offset?: number],
          { ok: boolean; base64?: string; kind?: string; error?: string;
            offset?: number; total?: number }>("fetch_artwork");
/** Write an icon to disk and get its path back.
 *
 *  Steam takes the four library slots as bytes, but takes a shortcut icon as a
 *  filesystem PATH — it reads that file every time it draws the icon. So this
 *  one slot has to become a real file, which only the Python side can do. */
export const saveIcon =
  guarded<[game_id: number, data: string, kind: string, app_id: number],
           { ok: boolean; path?: string; error?: string }>("save_icon");
/** The account's shelves, and which one this device is pointed at.
 *
 *  Asked of the gateway each time rather than cached: the shelf can be changed
 *  from the website or another device, and a stale copy would show the wrong
 *  library with nothing to reveal the mismatch. */
export const getShelves =
  guarded<[], { ok: boolean; shelves?: Array<{ id: number; name: string; games: number }>;
                 device_id?: number | null; shelf_id?: number | null;
                 device_name?: string | null; error?: string }>("get_shelves");

/** Point this device at a shelf, or at null for the shared games. */
export const setShelf =
  guarded<[shelf_id: number | null], { ok: boolean; shelf_id?: number | null; error?: string }>("set_shelf");

export const installControllerLayout =
  guarded<[shortcut_name: string, template_url: string],
           { ok: boolean; installed?: number; autosave_url?: string; error?: string }>("install_controller_layout");

// Keyed by "<game id>:<provider>", not by game id: one game can be several
// tiles. The backend still accepts a bare id and normalises it, so these two
// are safe against a plugin whose halves are briefly different versions.
export const recordShortcut =
  guarded<[key: string, app_id: number, name: string, options: string, art: string,
           provider: string, url: string], unknown>("record_shortcut");
export const forgetShortcut = guarded<[key: string], unknown>("forget_shortcut");

/** app id -> which service that tile launches, for the library badge.
 *
 *  Its own call rather than the whole library: this runs on a patched Steam
 *  route, once per game page opened, and it only needs one word. */
export const trackedProviders = guarded<[], {
  ok: boolean;
  providers?: Record<string, string>;
}>("tracked_providers");

/** Every Shelfcloud tile Steam actually has, read from shortcuts.vdf.
 *
 *  The plugin's own record can drift: a shortcut created but never written
 *  down is invisible to reconcile, and the next sync then makes a SECOND tile
 *  for the same game. This is the file's answer rather than the plugin's. */
export const strayShortcuts = guarded<[], {
  ok: boolean;
  error?: string;
  shortcuts: Array<{
    /** How the group is tracked: "<game id>:<provider>". Absent from an older
     *  backend, which grouped by game alone. */
    key?: string;
    game_id: number;
    /** Which service these tiles open. Two tiles for one game on the SAME
     *  service are twins; on different services they are both wanted. */
    provider?: string;
    app_ids: number[];
    /** The one to keep: the tracked tile if there is one, else the oldest. */
    keep: number;
    tracked?: number | null;
  }>;
}>("stray_shortcuts");

export const syncFinished =
  guarded<[added: number, removed: number, updated: number], unknown>("sync_finished");
export const applyOverrides =
  guarded<[], { ok: boolean; browser?: string; error?: string }>("apply_overrides");
