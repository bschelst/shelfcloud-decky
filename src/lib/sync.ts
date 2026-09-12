// Making the Steam library match what the user picked on the web.
//
// This lives on the frontend because only here does SteamClient exist. The
// backend decides *what* the library should contain; this makes it so.

import type { DesiredGame, SyncResult, TrackedShortcut } from "./types";

/** Steam library asset slots. See the note in shortcuts.ts on why these are local. */
const LibraryAsset = { Capsule: 0, Hero: 1 } as const;

const START_DIR = "/usr/bin/";

async function fetchArtworkBase64(url: string): Promise<string | undefined> {
  try {
    const response = await fetch(url);
    if (!response.ok) return undefined;
    const bytes = new Uint8Array(await response.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  } catch {
    return undefined;
  }
}

/** Apply cover and banner. Never fatal: a blank tile still plays. */
async function applyArtwork(appId: number, game: DesiredGame): Promise<void> {
  const artwork: Array<[string | undefined | null, number]> = [
    [game.cover_url, LibraryAsset.Capsule],
    [game.banner_url, LibraryAsset.Hero],
  ];
  for (const [url, assetType] of artwork) {
    if (!url) continue;
    const base64 = await fetchArtworkBase64(url);
    if (!base64) continue;
    try {
      const imageType = url.toLowerCase().includes(".png") ? "png" : "jpg";
      await SteamClient.Apps.SetCustomArtworkForApp(appId, base64, imageType, assetType as never);
    } catch {
      /* keep going */
    }
  }
}

export interface SyncCallbacks {
  record: (gameId: number, appId: number, name: string, options: string) => Promise<unknown>;
  forget: (gameId: number) => Promise<unknown>;
  onProgress?: (message: string) => void;
}

/**
 * Reconcile Steam shortcuts against the desired library.
 *
 * Three cases, driven by the tracked game_id -> appId map:
 *   - desired but untracked  -> create a tile
 *   - tracked but not desired -> the user removed it on the web; delete the tile
 *   - tracked and changed     -> provider or browser changed; update in place
 *
 * Updating in place rather than delete-and-recreate matters: recreating would
 * lose the user's playtime, controller layout and collections for that tile.
 */
export async function syncLibrary(
  desired: DesiredGame[],
  tracked: Record<string, TrackedShortcut>,
  callbacks: SyncCallbacks,
): Promise<SyncResult> {
  const result: SyncResult = { added: 0, removed: 0, updated: 0, failed: 0 };
  const desiredIds = new Set(desired.map((g) => g.game_id));

  for (const game of desired) {
    const existing = tracked[String(game.game_id)];
    try {
      if (!existing) {
        callbacks.onProgress?.(`Adding ${game.name}…`);
        const appId = await SteamClient.Apps.AddShortcut(
          game.name, game.exe, START_DIR, game.options,
        );
        if (!appId) throw new Error("Steam refused the shortcut");

        // AddShortcut's name argument does not stick: immediately afterwards the
        // app overview has no display_name, and Steam later fills it from the
        // executable's basename — so every tile ended up called "python3".
        // Setting it explicitly is what actually names the tile.
        SteamClient.Apps.SetShortcutName(appId, game.name);

        await applyArtwork(appId, game);
        await callbacks.record(game.game_id, appId, game.name, game.options);
        result.added++;
      } else if (existing.launch !== game.options) {
        // Same game, different launch command (provider or browser changed).
        callbacks.onProgress?.(`Updating ${game.name}…`);
        SteamClient.Apps.SetShortcutLaunchOptions(existing.app_id, game.options);
        SteamClient.Apps.SetShortcutExe(existing.app_id, game.exe);
        await callbacks.record(game.game_id, existing.app_id, game.name, game.options);
        result.updated++;
      }
    } catch (error) {
      console.error("[shelfcloud] sync failed for", game.name, error);
      result.failed++;
    }
  }

  for (const [gameId, entry] of Object.entries(tracked)) {
    if (desiredIds.has(Number(gameId))) continue;
    try {
      callbacks.onProgress?.(`Removing ${entry.name}…`);
      SteamClient.Apps.RemoveShortcut(entry.app_id);
      await callbacks.forget(Number(gameId));
      result.removed++;
    } catch (error) {
      console.error("[shelfcloud] could not remove", entry.name, error);
      result.failed++;
    }
  }

  return result;
}
