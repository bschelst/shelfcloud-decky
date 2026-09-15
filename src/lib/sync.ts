// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// Making the Steam library match what the user picked on the web.
//
// This lives on the frontend because only here does SteamClient exist. The
// backend decides *what* the library should contain; this makes it so.

import { fetchArtwork, saveIcon } from "./api";
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

/** Games whose artwork could not be applied during this plugin session.
 *
 *  Retrying a failed slot is right — a CORS fix or better art upstream should
 *  land without the user doing anything — but retrying it every 60 seconds
 *  forever is not. Some games (unreleased titles) have no artwork anywhere, and
 *  those were being "updated" on every single sync. Session-scoped, so a plugin
 *  reload gives every game one more try. */
const artUnavailable = new Set<number>();

/** Ask the Python backend for an image, already base64-encoded.
 *
 *  Not fetch(): this code runs inside Steam's client at
 *  https://steamloopback.host, so every artwork URL is cross-origin. Steam's
 *  own CDN sends access-control-allow-origin and worked; SteamGridDB sends no
 *  CORS header, and the gateway is plain HTTP which the client blocks as mixed
 *  content. Both failed silently, leaving blank tiles with nothing logged.
 *  Python is subject to neither rule. */
async function fetchArtworkBase64(url: string): Promise<{ data: string; kind: "png" | "jpg" } | undefined> {
  try {
    const result = await fetchArtwork(url);
    if (!result?.ok || !result.base64) return undefined;
    return { data: result.base64, kind: result.kind === "png" ? "png" : "jpg" };
  } catch {
    return undefined;
  }
}

type ArtSlot = "capsule" | "header" | "hero" | "logo";

/** The slot shown on the tile itself, and the ones behind it.
 *
 *  Capsule is separated out because it is the only one visible in the library
 *  grid. Header is the wide banner Steam shows for the *selected* game —
 *  setting only the capsule leaves that big panel blank, which reads as
 *  "artwork is broken" even though the small tile looks fine. Hero and logo
 *  back the detail page.
 */
const VISIBLE_SLOT: [ArtSlot, number] = ["capsule", LibraryAsset.Capsule];
const BACKGROUND_SLOTS: Array<[ArtSlot, number]> = [
  ["header", LibraryAsset.Header],
  ["hero", LibraryAsset.Hero],
  ["logo", LibraryAsset.Logo],
];

/** Download one slot's image, preferring the direct URL over the gateway.
 *
 *  Steam's own CDN sends access-control-allow-origin, so most art needs no
 *  detour; SteamGridDB sends none, and this runs inside Steam's client, so the
 *  browser throws the response away. That silently blanked every cloud-only
 *  game. The gateway can serve art whose own host is unreachable or blocked,
 *  but it is an extra hop, so it is the fallback.
 */
async function fetchSlot(game: DesiredGame, slot: ArtSlot) {
  const direct = game.artwork?.[slot];
  if (!direct) return undefined;
  const image = await fetchArtworkBase64(direct);
  if (image) return image;
  const viaGateway = game.artwork_proxy?.[slot];
  return viaGateway ? fetchArtworkBase64(viaGateway) : undefined;
}

/** What came of one game's artwork.
 *
 *  `applied` counts every slot that went on, including the icon. `visible` is
 *  narrower and is the one that decides whether we are finished: did the
 *  CAPSULE — the only slot drawn in the library grid — get its picture?
 *
 *  They are separate because a count cannot tell them apart. With the capsule
 *  failing and hero and logo succeeding, `applied` is 2, which read as success,
 *  the fingerprint was stored, and the tile was never retried. The result is a
 *  blank tile whose detail page has full artwork — which is exactly what
 *  "007 First Light" looked like: hero and logo on, grid blank, and the plugin
 *  certain it had finished the job.
 */
interface ArtworkOutcome {
  applied: number;
  visible: boolean;
}

/** Fill each library art slot. Never fatal: a blank tile still plays.
 *
 *  Returns how many slots were actually applied, which is not the same as how
 *  many were attempted — see recordArtwork below.
 *
 *  Two deliberate choices about ordering, both about how "add all my games"
 *  feels rather than how fast it finishes:
 *
 *  The capsule is applied first and alone, because it is the only slot visible
 *  in the library grid. Fetching all four before applying any would leave the
 *  tile blank for four round trips; doing the visible one first fills it as
 *  soon as it possibly can.
 *
 *  The other three are fetched concurrently. They used to be sequential, so
 *  every game cost four round trips end to end and artwork trickled in for
 *  minutes on a large add. They are independent downloads that nothing orders,
 *  so waiting for each in turn bought nothing.
 *
 *  Fetching is parallel; applying is not. SetCustomArtworkForApp writes to
 *  Steam's own library state, and firing several at one appId at once is not
 *  something Steam documents as safe. The downloads are where the time goes,
 *  so there is nothing to gain by racing the writes as well.
 */
async function applyArtwork(appId: number, game: DesiredGame): Promise<ArtworkOutcome> {
  let applied = 0;

  const applySlot = async (
    image: Awaited<ReturnType<typeof fetchSlot>>,
    assetType: number,
  ) => {
    if (!image) return false;
    try {
      await SteamClient.Apps.SetCustomArtworkForApp(appId, image.data, image.kind, assetType as never);
      applied += 1;
      return true;
    } catch {
      /* keep going: one bad slot must not cost the others */
      return false;
    }
  };

  const [visibleSlot, visibleType] = VISIBLE_SLOT;

  // Started before the capsule is awaited, so these download during it rather
  // than after it. Without this line they would be parallel with each other
  // but still strictly after the capsule finished.
  const background = Promise.all(BACKGROUND_SLOTS.map(([slot]) => fetchSlot(game, slot)));

  const capsule = await fetchSlot(game, visibleSlot);
  const capsuleWentOn = await applySlot(capsule, visibleType);

  // Whether the tile the user actually looks at got its picture.
  //
  // "No capsule to apply" counts as settled: the gateway has no grid art for
  // this game, so retrying every sync forever would change nothing. A capsule
  // that EXISTS and failed does not count, and that is the whole point — see
  // ArtworkOutcome.
  const visible = capsuleWentOn || !game.artwork?.[visibleSlot];

  const images = await background;
  for (let i = 0; i < BACKGROUND_SLOTS.length; i++) {
    await applySlot(images[i], BACKGROUND_SLOTS[i][1]);
  }

  // The icon — a different mechanism from everything above.
  //
  // The first attempt wrote it as library asset type 4 through
  // SetCustomArtworkForApp. That value was inferred from a comment, never
  // verified, and artwork vanished from tiles afterwards. Probing the live
  // client showed why: Steam exposes a SEPARATE call for this.
  //
  //    SetCustomArtworkForApp   the four library slots, given bytes
  //    SetShortcutIcon          shortcut icons, given a FILE PATH
  //
  // An icon is not a library asset at all. It lives in shortcuts.vdf as a
  // path, which is why the bytes have to be written to disk by the backend
  // first. Verified on hardware: the path lands in shortcuts.vdf and all four
  // artwork files survive untouched.
  //
  // The logo is used because it is closest to an icon — small, transparent,
  // centred — and it is already downloaded. Capsule is the fallback.
  const logoIndex = BACKGROUND_SLOTS.findIndex(([slot]) => slot === "logo");
  const iconImage = (logoIndex >= 0 ? images[logoIndex] : undefined) ?? capsule;
  if (iconImage) {
    try {
      const saved = await saveIcon(game.game_id, iconImage.data, iconImage.kind);
      if (saved?.ok && saved.path) {
        SteamClient.Apps.SetShortcutIcon(appId, saved.path);
        applied += 1;
      }
    } catch {
      /* an icon is cosmetic; never let it cost the artwork that worked */
    }
  }

  return { applied, visible };
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
 * Bump when the SET of slots this plugin writes changes.
 *
 * The fingerprint is built from artwork URLs, so adding a slot changes nothing
 * about it — every existing tile keeps matching and is never revisited. Adding
 * the icon would then only reach tiles created afterwards, leaving every
 * current tile on Steam's default forever. Changing this invalidates every
 * stored fingerprint exactly once, so the next sync re-applies artwork
 * everywhere and then goes quiet again.
 *
 * 1 -> 2: started writing LibraryAsset.Icon.
 * 2 -> 3: reverted that. Bumped AGAIN rather than back to "1", because tiles
 *         written under revision 2 may have had their custom art damaged by
 *         the icon write. Going back to "1" would leave exactly those tiles
 *         matching their stored fingerprint and never repaired.
 * 3 -> 4: icons again, via SetShortcutIcon this time — the call Steam actually
 *         provides for them, verified on hardware.
 * 4 -> 5: not a new slot — a repair. Tiles whose capsule failed while their
 *         hero and logo succeeded were recorded as finished, so their stored
 *         fingerprint still matches the art they were meant to get and nothing
 *         would ever look at them again. Bumping is the only way to reach a
 *         tile that is already wrongly marked done.
 */
const ART_REVISION = "5";

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
  return [ART_REVISION, a.capsule ?? "", a.header ?? "", a.hero ?? "", a.logo ?? ""].join("|");
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
  const result: SyncResult = { added: 0, removed: 0, updated: 0, failed: 0, addedNames: [] };
  const desiredIds = new Set(desired.map((g) => g.game_id));

  // Tiles are sorted into their provider's collection as the sync runs, in
  // batches, rather than all at once when it finishes.
  //
  // Two reasons. A sync that is interrupted — the panel closed, Steam
  // restarted, an error part way — used to sort nothing at all, because the
  // single call came after the loop; now everything up to the last batch is
  // already filed. And saving a collection is what makes Steam re-read its
  // library, so batching spreads that out instead of doing it once at the end.
  //
  // Batched rather than per-game because Save() rewrites the whole collection:
  // per-game would mean one full rewrite per tile.
  const COLLECTION_BATCH = 12;
  let pending: CollectionMember[] = [];

  const flushCollections = async () => {
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    await addToProviderCollections(batch);
  };

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

        // Record the fingerprint only once the VISIBLE slot went on. It used to
        // be stored whenever any slot did, so a game whose capsule failed but
        // whose hero and logo succeeded counted as done — a blank tile with a
        // fully dressed detail page, never retried.
        const appliedNew = await applyArtwork(appId, game);
        if (appliedNew.applied === 0) artUnavailable.add(game.game_id);
        await callbacks.record(game.game_id, appId, title, game.options,
                               appliedNew.visible ? art : "", game.provider);
        pending.push({ appId, provider: game.provider });
        result.added++;
        result.addedNames.push(game.name);
        // File this batch now, so the tiles are visible in their collection
        // while the rest of the sync is still running.
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
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
        // Do not re-attempt art already known to be unavailable this session.
        const tryArt = artChanged && !artUnavailable.has(game.game_id);

        let appliedArt: ArtworkOutcome = { applied: 0, visible: false };
        if (tryArt) {
          if (!launchChanged) step("Artwork", game.name);
          appliedArt = await applyArtwork(existing.app_id, game);
          if (appliedArt.applied === 0) artUnavailable.add(game.game_id);
        }
        if (launchChanged || tryArt) {
          // Keep the old fingerprint until the visible slot goes on, so a later
          // session tries again rather than marking a blank tile as finished.
          const recordedArt = tryArt && !appliedArt.visible ? (existing.art ?? "") : art;
          await callbacks.record(game.game_id, existing.app_id, title, game.options,
                                 recordedArt, game.provider);
          result.updated++;
        }
        pending.push({ appId: existing.app_id, provider: game.provider });
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
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

  // Whatever did not fill a batch. Best effort — unsorted tiles still play.
  await flushCollections();

  return result;
}
