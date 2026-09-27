// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Calls into the plugin's Python backend (main.py). Declare every method here
// through guarded(). Never use callable() directly: it skips the payload check,
// the call deadline and the concurrency limit. See docs/RPC-PAYLOAD-LIMITS.md.

import { guarded } from "./rpc";

import type {
  AddOwnedResult, LibraryResponse, LoginPoll, LoginStart, OwnedPreview,
  SteamLibraryResult, Status,
} from "./types";

// Put a line in the plugin's log file. The sync's console output goes to a CEF
// console nobody reads, and the question is always asked afterwards.
export const logNote = guarded<[message: string], { ok: boolean }>("log_note", true);

export const getStatus = guarded<[], Status>("get_status");

// Fill in the Steam appid on existing tiles, in one call. record_shortcut writes
// it too, but only for tiles a sync touches, so on a settled library it stayed empty.
export const setSteamIds =
  guarded<[ids: Record<string, string>], { ok: boolean; updated?: number }>(
    "set_steam_ids", true);

// How many people play this game on Steam right now. ok=false when the game is
// not on Steam at all (one in six), which is not the same as nobody playing.
export const currentPlayers =
  guarded<[steam_app_id: string],
          { ok: boolean; players?: number; error?: string }>("current_players");
export const setSetting = guarded<[key: string, value: unknown], Status>("set_setting");

// Start installing one of the offered browsers. Returns once it has started;
// progress lives in `status.install`, so closing the panel does not lose it.
export const installBrowser =
  guarded<[app_id: string], { ok: boolean; error?: string }>("install_browser");
// clear a finished install's message so the panel can move on.
export const dismissInstall = guarded<[], Status>("dismiss_install");

export const startLogin = guarded<[], LoginStart>("start_login");
export const pollLogin = guarded<[session_token: string], LoginPoll>("poll_login");
export const logout = guarded<[], { ok: boolean }>("logout");

export const clearLibrary = guarded<[], { ok: boolean; removed?: number; error?: string }>("clear_library");
export const previewOwned = guarded<[], OwnedPreview>("preview_owned");
export const addOwnedGames = guarded<[], AddOwnedResult>("add_owned_games");
// Take games back out of the library, for a cancelled bulk add. the add put every
// owned game in at once, so only games added but never tiled are passed here.
export const dropFromLibrary =
  guarded<[game_ids: number[]], { ok: boolean; removed?: number; error?: string }>(
    "drop_from_library");
// Put games back, for a cancelled "remove all". that run empties the library on the
// server first, so only tiles that still exist are restored.
export const restoreToLibrary =
  guarded<[game_ids: number[]], { ok: boolean; restored?: number; error?: string }>(
    "restore_to_library");
export const syncSteamLibrary = guarded<[], SteamLibraryResult>("sync_steam_library");
// What the library should contain. pass the version you last got: when nothing
// changed the backend says so in a few bytes, not the whole 1.6 MB list.
export const getDesiredLibrary =
  guarded<[known?: string], LibraryResponse>("get_desired_library", true);
// Artwork is downloaded in Python, not the browser: inside Steam every image URL is
// cross-origin and our gateway is plain HTTP (mixed content). Python has neither limit.
export const fetchArtwork =
  guarded<[url: string, offset?: number],
          { ok: boolean; base64?: string; kind?: string; error?: string;
            offset?: number; total?: number }>("fetch_artwork", true);
// Write an icon to disk and get its path back. Steam takes a shortcut icon as a file
// PATH it reads each draw, so this slot must be a real file, which only Python makes.
export const saveIcon =
  guarded<[game_id: number, data: string, kind: string, app_id: number],
           { ok: boolean; path?: string; error?: string }>("save_icon", true);
// Write an icon the backend fetches itself, named by URL. The preferred one: saveIcon
// sends the image as base64 back over the socket unsliced, and a big icon could pass
// the 4 MB cap and freeze the sync. naming it by URL costs a few bytes and no refetch.
export const saveIconFromUrl =
  guarded<[game_id: number, url: string, app_id: number],
           { ok: boolean; path?: string; error?: string }>("save_icon_from_url", true);
// The account's shelves, and which one this device points at. asked fresh, not cached:
// the shelf can change from the website or another device.
export const getShelves =
  guarded<[], { ok: boolean; shelves?: Array<{ id: number; name: string; games: number }>;
                 device_id?: number | null; shelf_id?: number | null;
                 device_name?: string | null; error?: string }>("get_shelves");

// Point this device at a shelf, or at null for the shared games.
export const setShelf =
  guarded<[shelf_id: number | null], { ok: boolean; shelf_id?: number | null; error?: string }>("set_shelf");

// Where the installed layouts live, for selecting them. installing writes files so it
// only runs for a touched tile; selecting is a pure Steam call that must run for every
// tile, since Steam never picks our config up on its own. one call per sync, the caller
// builds a path per tile from the base.
export const layoutSelectionBase =
  guarded<[], { ok: boolean; base?: string; native?: string; repair?: boolean;
                repair_rev?: number; error?: string }>("layout_selection_base", true);

export const installControllerLayout =
  guarded<[shortcut_name: string, template_url: string],
           { ok: boolean; installed?: number; autosave_url?: string; error?: string }>("install_controller_layout", true);

// Keyed by "<game id>:<provider>", not game id: one game can be several tiles. the
// backend also takes a bare id and normalises it, safe if the two halves differ in version.
export const recordShortcut =
  guarded<[key: string, app_id: number, name: string, options: string, art: string,
           provider: string, url: string], unknown>("record_shortcut", true);
export const forgetShortcut = guarded<[key: string], unknown>("forget_shortcut", true);

// app id -> which service that tile launches, for the library badge. Its own call
// and not the whole library: it runs once per game page opened and needs one word.
export const trackedProviders = guarded<[], {
  ok: boolean;
  providers?: Record<string, string>;
  // app id -> the game's id on Steam, absent for the one game in six not on Steam.
  steam_ids?: Record<string, string>;
}>("tracked_providers");

// Every Shelfcloud tile Steam really has, read from shortcuts.vdf. the plugin's own
// record can drift, so a shortcut made but never written down would get a second tile.
export const strayShortcuts = guarded<[], {
  ok: boolean;
  error?: string;
  shortcuts: Array<{
    // how the group is tracked: "<game id>:<provider>". absent from an older backend.
    key?: string;
    game_id: number;
    // which service these tiles open. two on the same service are twins; on different
    // services both are wanted.
    provider?: string;
    app_ids: number[];
    // the one to keep: the tracked tile if there is one, else the oldest.
    keep: number;
    tracked?: number | null;
  }>;
}>("stray_shortcuts", true);

export const syncFinished =
  guarded<[added: number, removed: number, updated: number], unknown>("sync_finished");
export const applyOverrides =
  guarded<[], { ok: boolean; browser?: string; error?: string }>("apply_overrides");
