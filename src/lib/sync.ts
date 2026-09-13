// Making the Steam library match what the user picked on the web.
//
// This lives on the frontend because only here does SteamClient exist. The
// backend decides *what* the library should contain; this makes it so.

import { addToProviderCollections, type CollectionMember } from "./collection";
import { providerLabel, type DesiredGame, type SyncResult, type TrackedShortcut } from "./types";

/**
 * Tile name, suffixed with the provider.
 *
 * Without this a cloud tile sits next to the user's real copy under an
 * identical name — two "Cyberpunk 2077" entries with no way to tell which
 * streams and which is installed locally.
 */
export function shortcutTitle(game: DesiredGame): string {
  return `${game.name} (${providerLabel(game.provider)})`;
}

/**
 * Steam library asset slots.
 *
 * Declared locally because @decky/ui does not re-export ELibraryAssetType from
 * its package root; these are the upstream enum's implicit values.
 */
const LibraryAsset = { Capsule: 0, Hero: 1, Logo: 2, Header: 3, Icon: 4 } as const;

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

/** Fill each library art slot. Never fatal: a blank tile still plays. */
async function applyArtwork(appId: number, game: DesiredGame): Promise<void> {
  const artwork: Array<[string | undefined | null, number]> = [
    [game.artwork?.capsule, LibraryAsset.Capsule],
    // Header is the wide banner Steam shows for the *selected* game. Setting
    // only the capsule leaves that big panel blank, which reads as "artwork is
    // broken" even though the small tile looks fine.
    [game.artwork?.header, LibraryAsset.Header],
    [game.artwork?.hero, LibraryAsset.Hero],
    [game.artwork?.logo, LibraryAsset.Logo],
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
  record: (
    gameId: number, appId: number, name: string, options: string, art: string,
    provider: string,
  ) => Promise<unknown>;
  forget: (gameId: number) => Promise<unknown>;
  onProgress?: (message: string) => void;
}

/**
 * A fingerprint of the artwork a tile currently carries.
 *
 * Stored alongside the shortcut so sync can tell when the gateway has resolved
 * better art and re-apply it. Without this, artwork is only ever applied at
 * creation and existing tiles stay frozen — which is how games resolved after
 * their tile was made ended up blank.
 */
export function artworkFingerprint(game: DesiredGame): string {
  const a = game.artwork ?? {};
  return [a.capsule ?? "", a.header ?? "", a.hero ?? "", a.logo ?? ""].join("|");
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
  const members: CollectionMember[] = [];

  // Count the REAL work up front — adds, updates and removals — not every game
  // walked past. Counting all of them made the denominator (308) disagree with
  // the button that started the job ("Add 124 playable games"), which reads as
  // a bug even though both numbers were correct.
  //
  // Unchanged games do not need to tick the counter: they are skipped with no
  // network calls, so hundreds pass instantly and there is no stall to hide.
  const toRemove = Object.keys(tracked).filter((id) => !desiredIds.has(Number(id))).length;
  const toChange = desired.filter((g) => {
    const existing = tracked[String(g.game_id)];
    if (!existing) return true;                                  // add
    if (existing.launch !== g.options) return true;              // relaunch
    if (existing.name !== shortcutTitle(g)) return true;         // renamed
    return (existing.art ?? "") !== artworkFingerprint(g);       // new artwork
  }).length;
  const total = toChange + toRemove;
  let done = 0;
  // Game names vary wildly in length, and a message that wraps to two lines
  // and back makes the whole panel jump as it ticks through hundreds of games.
  // Truncating keeps every progress line the same height.
  const shorten = (name: string) => (name.length > 22 ? `${name.slice(0, 21)}…` : name);
  const step = (label: string, name: string) => {
    done++;
    callbacks.onProgress?.(`${label} ${shorten(name)} (${done}/${total})`);
  };

  for (const game of desired) {
    const existing = tracked[String(game.game_id)];
    const title = shortcutTitle(game);
    const art = artworkFingerprint(game);
    try {
      if (!existing) {
        step("Adding", game.name);
        const appId = await SteamClient.Apps.AddShortcut(
          title, game.exe, START_DIR, game.options,
        );
        if (!appId) throw new Error("Steam refused the shortcut");

        // AddShortcut only reliably applies the exe — and it stores that
        // quote-wrapped as "/usr/bin/python3". The name and launchOptions
        // arguments are silently dropped, which produced tiles called "python3"
        // that ran an argument-less interpreter and exited instantly.
        // Verified against shortcuts.vdf on hardware. Set everything explicitly.
        SteamClient.Apps.SetShortcutName(appId, title);
        SteamClient.Apps.SetShortcutExe(appId, game.exe);
        SteamClient.Apps.SetShortcutStartDir(appId, START_DIR);
        SteamClient.Apps.SetShortcutLaunchOptions(appId, game.options);

        await applyArtwork(appId, game);
        await callbacks.record(game.game_id, appId, title, game.options, art, game.provider);
        members.push({ appId, provider: game.provider });
        result.added++;
      } else {
        const launchChanged = existing.launch !== game.options || existing.name !== title;
        // Artwork is compared separately: the gateway resolves better art over
        // time, and a tile whose launch command never changes must still pick
        // it up rather than staying blank forever.
        const artChanged = (existing.art ?? "") !== art;

        if (launchChanged) {
          step("Updating", game.name);
          SteamClient.Apps.SetShortcutName(existing.app_id, title);
          SteamClient.Apps.SetShortcutExe(existing.app_id, game.exe);
          SteamClient.Apps.SetShortcutStartDir(existing.app_id, START_DIR);
          SteamClient.Apps.SetShortcutLaunchOptions(existing.app_id, game.options);
        }
        if (artChanged) {
          if (!launchChanged) step("Artwork", game.name);
          await applyArtwork(existing.app_id, game);
        }
        if (launchChanged || artChanged) {
          await callbacks.record(game.game_id, existing.app_id, title, game.options, art, game.provider);
          result.updated++;
        }
        members.push({ appId: existing.app_id, provider: game.provider });
      }
    } catch (error) {
      console.error("[shelfcloud] sync failed for", game.name, error);
      result.failed++;
    }
  }

  for (const [gameId, entry] of Object.entries(tracked)) {
    if (desiredIds.has(Number(gameId))) continue;
    try {
      step("Removing", entry.name);
      SteamClient.Apps.RemoveShortcut(entry.app_id);
      await callbacks.forget(Number(gameId));
      result.removed++;
    } catch (error) {
      console.error("[shelfcloud] could not remove", entry.name, error);
      result.failed++;
    }
  }

  // Sort tiles into a collection per provider, so the user can see at a glance
  // what runs where. Best effort — unsorted tiles still play.
  await addToProviderCollections(members);

  return result;
}
