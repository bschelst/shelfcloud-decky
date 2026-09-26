// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Calls into the plugin's Python backend (main.py).
//
// Declare every backend method here, through guarded(). Nothing should reach
// for callable() directly: that bypasses the payload size check, the call
// deadline and the concurrency limit. See docs/RPC-PAYLOAD-LIMITS.md.

import { guarded } from "./rpc";

import type {
  AddOwnedResult, LibraryResponse, LoginPoll, LoginStart, OwnedPreview,
  SteamLibraryResult, Status,
} from "./types";

/** Put a line in the plugin's log file.
 *
 *  The sync runs where console output goes to a CEF console nobody reads, and
 *  the interesting question is always asked after the fact. */
export const logNote = guarded<[message: string], { ok: boolean }>("log_note", true);

export const getStatus = guarded<[], Status>("get_status");

/** Fill in the Steam appid on tiles that already exist.
 *
 *  One call for the whole library. record_shortcut writes this field too, but
 *  only for tiles a sync actually touches — which on a settled library is none
 *  of them, so the field stayed empty on all 890 and the player badge had
 *  nothing to ask about. */
export const setSteamIds =
  guarded<[ids: Record<string, string>], { ok: boolean; updated?: number }>(
    "set_steam_ids", true);

/** How many people are playing this game on Steam right now.
 *
 *  ok=false when the game is not on Steam at all — one in six of the
 *  catalogue — which is a different thing from nobody playing it. */
export const currentPlayers =
  guarded<[steam_app_id: string],
          { ok: boolean; players?: number; error?: string }>("current_players");
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
export const getDesiredLibrary = guarded<[], LibraryResponse>("get_desired_library", true);
/** Artwork is downloaded by the Python backend, not the browser: inside Steam's
 *  client every image URL is cross-origin, and our own gateway is plain HTTP
 *  (mixed content). Python has neither restriction. */
export const fetchArtwork =
  guarded<[url: string, offset?: number],
          { ok: boolean; base64?: string; kind?: string; error?: string;
            offset?: number; total?: number }>("fetch_artwork", true);
/** Write an icon to disk and get its path back.
 *
 *  Steam takes the four library slots as bytes, but takes a shortcut icon as a
 *  filesystem PATH — it reads that file every time it draws the icon. So this
 *  one slot has to become a real file, which only the Python side can do. */
export const saveIcon =
  guarded<[game_id: number, data: string, kind: string, app_id: number],
           { ok: boolean; path?: string; error?: string }>("save_icon", true);
/** Write an icon the backend fetches ITSELF, named by URL. The preferred one.
 *
 *  saveIcon takes the image as base64, which pushes the whole picture back
 *  across Decky's websocket — the only large message travelling THIS direction
 *  and the only one never sliced, while the same picture coming the other way
 *  has been chunked at 192 KB all along. aiohttp closes a websocket on a
 *  message over 4 MB rather than raising, and a closed socket does not fail a
 *  call, it silences it: nothing settles, so not even the timeout can report
 *  it. That is the sync freeze, and it was reached by any game whose icon fell
 *  back to its 600x900 capsule.
 *
 *  The backend downloaded that image already — it is what served the slices —
 *  so naming it costs a few hundred bytes and no second fetch. saveIcon stays
 *  for images DRAWN here, which have no address and are kilobytes anyway. */
export const saveIconFromUrl =
  guarded<[game_id: number, url: string, app_id: number],
           { ok: boolean; path?: string; error?: string }>("save_icon_from_url", true);
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

/** Where the installed layouts live, for SELECTING them.
 *
 *  Installing a layout and selecting it are different costs. Installing writes
 *  files, so it only runs for a tile being touched; selecting is a pure
 *  Steam-client call and has to run for EVERY tile, because Steam never picks
 *  our config up on its own. Tying the two together meant the selection only
 *  ever ran when a tile was created, so a settled library quietly fell back to
 *  Steam's default layout on all 844 of them.
 *
 *  One call per sync; the caller composes a path per tile from the base. */
export const layoutSelectionBase =
  guarded<[], { ok: boolean; base?: string; native?: string; repair?: boolean;
                repair_rev?: number; error?: string }>("layout_selection_base", true);

export const installControllerLayout =
  guarded<[shortcut_name: string, template_url: string],
           { ok: boolean; installed?: number; autosave_url?: string; error?: string }>("install_controller_layout", true);

// Keyed by "<game id>:<provider>", not by game id: one game can be several
// tiles. The backend still accepts a bare id and normalises it, so these two
// are safe against a plugin whose halves are briefly different versions.
export const recordShortcut =
  guarded<[key: string, app_id: number, name: string, options: string, art: string,
           provider: string, url: string], unknown>("record_shortcut", true);
export const forgetShortcut = guarded<[key: string], unknown>("forget_shortcut", true);

/** app id -> which service that tile launches, for the library badge.
 *
 *  Its own call rather than the whole library: this runs on a patched Steam
 *  route, once per game page opened, and it only needs one word. */
export const trackedProviders = guarded<[], {
  ok: boolean;
  providers?: Record<string, string>;
  /** app id -> the game's id on STEAM, absent for the one game in six that
   *  is not on Steam and so can have no player count. */
  steam_ids?: Record<string, string>;
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
}>("stray_shortcuts", true);

export const syncFinished =
  guarded<[added: number, removed: number, updated: number], unknown>("sync_finished");
export const applyOverrides =
  guarded<[], { ok: boolean; browser?: string; error?: string }>("apply_overrides");
