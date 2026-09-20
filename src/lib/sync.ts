// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Making the Steam library match what the user picked on the web.
//
// This lives on the frontend because only here does SteamClient exist. The
// backend decides *what* the library should contain; this makes it so.

import { fetchArtwork, saveIcon, strayShortcuts } from "./api";
import { addToProviderCollections, pruneProviderCollections,
         removeRetiredCollections, type CollectionMember } from "./collection";
import { renderPageCapsule } from "./pagecapsule";
import { renderPageLogo } from "./pagelogo";
import { providerLabel, WEBSITE_PROVIDERS, type DesiredGame, type SyncResult, type TrackedShortcut } from "./types";

/**
 * Tile name, suffixed with the provider.
 *
 * Without this a cloud tile sits next to the user's real copy under an
 * identical name — two "Cyberpunk 2077" entries with no way to tell which
 * streams and which is installed locally.
 */
/**
 * Which games a cancelled bulk add should hand back.
 *
 * Cancel means "stop, and forget the rest". "Add all my games" puts every
 * owned game into the library in ONE server call and then builds tiles one at
 * a time, so cancelling the tiles alone left the library holding hundreds of
 * games that simply appeared on the next sync — the cancel stopped that run
 * and changed nothing that lasted.
 *
 * Pure, and separated from the code that performs the removal, because this is
 * the part that decides what gets DELETED. Three conditions, all required:
 *
 *   - absent from `before`, so this run is what added it. A game the person
 *     chose last week is not this run's to take away.
 *   - untracked, so no tile was made for it. Anything tiled is a thing they
 *     can now see and was completed.
 *   - not a web page. Those are mandatory or hand-typed, exempt from the
 *     cancel in the first place, and must never be removed by one.
 *
 * `before === null` means the library could not be read, and yields nothing:
 * with no way to tell old from new, the safe answer is to remove none rather
 * than guess and take away somebody's library.
 */
export function untiledFromRun(
  before: Set<number> | null,
  desired: Array<{ game_id: number; provider?: string }>,
  tracked: Record<string, unknown>,
): number[] {
  if (!before || !Array.isArray(desired)) return [];
  return desired
    .filter((game) => game && typeof game === "object")
    .filter((game) => !WEBSITE_PROVIDERS.has(String(game.provider ?? "")))
    .map((game) => Number(game.game_id))
    .filter((id) => Number.isInteger(id)
      && !before.has(id)
      && !(String(id) in tracked));
}

export function shortcutTitle(game: DesiredGame): string {
  // A web page carries no suffix. The suffix exists to separate a streamed
  // copy of a game from an installed one under the same name, and a page has
  // no installed counterpart to be confused with — so all it added was noise
  // in the one place there is least room for it: "Shelfcloud Website
  // (shelfcloud)" is most of a Steam tile's title spent saying nothing.
  if (WEBSITE_PROVIDERS.has(game.provider)) return game.name;
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

type ArtSlot = "capsule" | "header" | "hero" | "logo" | "icon";

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
  const viaGateway = game.artwork_proxy?.[slot];

  // No direct URL is NOT the same as no artwork. The gateway serves some slots
  // from its own disk rather than from a catalogue address — website tiles are
  // the whole of that case — so the row can carry a null capsule while
  // /api/artwork/<id>/capsule answers with a picture. Returning early here
  // skipped the fallback for exactly the tiles that only have a fallback.
  //
  // A slot with neither is a slot with no artwork, which is normal: website
  // tiles deliberately have no logo, so that one answers 404 and stays empty,
  // which is how Steam knows to draw the name.
  if (direct) {
    const image = await fetchArtworkBase64(direct);
    if (image) return image;
  }
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
/** Take a tile's artwork with it when the tile goes.
 *
 *  Steam keys custom artwork to the app id and never tidies it: removing a
 *  shortcut leaves four files behind in userdata/<user>/config/grid forever.
 *  Nothing points at them afterwards, so nothing ever will.
 *
 *  Measured on a real Deck before this existed: 18,728 files and 4.3 GB in
 *  that folder, of which 17,155 files and 4.16 GB belonged to 4,339 app ids
 *  that no longer existed. Every recreation of the library wrote a fresh set
 *  and abandoned the last. A directory that size is also enough to break other
 *  plugins that enumerate it — SteamGridDB was crashing on it.
 *
 *  Best effort per slot: a tile that fails to clear is a few kilobytes left
 *  behind, and it must never stop the removal it belongs to.
 */
async function clearSlot(appId: number, assetType: number): Promise<void> {
  try {
    await SteamClient.Apps.ClearCustomArtworkForApp(appId, assetType as never);
  } catch {
    /* already gone, or Steam refused this slot; the others still matter */
  }
}

async function clearArtwork(appId: number): Promise<void> {
  // Capsule, Hero, Logo, Header — the four this plugin writes. Icon is a file
  // path set through SetShortcutIcon and is cleaned up by the backend.
  for (const assetType of [0, 1, 2, 3]) {
    await clearSlot(appId, assetType);
  }
}

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
  // Started here so it downloads with the others, but deliberately NOT in
  // BACKGROUND_SLOTS: everything in that list is handed to Steam as a library
  // asset, and an icon is not one. It is a file path, applied further down.
  const iconFetch = fetchSlot(game, "icon");

  const capsule = await fetchSlot(game, visibleSlot);

  // A website tile's capsule is drawn here, with its own name on it.
  //
  // The gateway ships ONE capsule and every website tile gets it: our cloud,
  // centred, on navy, with no words on it anywhere. A single page like that is
  // fine; a shelf of them is a wall of identical squares, and the only way to
  // tell which is which is to move the cursor onto one and read the title
  // Steam prints somewhere else. The published page has exactly the same
  // problem as a page somebody added, so both providers are covered.
  //
  // Drawn from the ICON and nothing else. The icon is the 256px square mark,
  // which is what a mark drawn into a square wants. The capsule is not a
  // substitute for it: it is 600x900 with the cloud already centred in it and
  // navy all round, so squeezing that into the square would give a squashed
  // cloud in a box — worse than the plain tile this replaces. Without an icon
  // the gateway's own capsule stays, which is what the tile had before.
  let visibleImage = capsule;
  if (WEBSITE_PROVIDERS.has(game.provider)) {
    const mark = await iconFetch;
    if (mark) {
      const drawn = await renderPageCapsule(game.name, mark);
      if (drawn) visibleImage = drawn;
    }
  }

  const capsuleWentOn = await applySlot(visibleImage, visibleType);

  // Whether the tile the user actually looks at got its picture.
  //
  // "No capsule to apply" counts as settled: the gateway has no grid art for
  // this game, so retrying every sync forever would change nothing. A capsule
  // that EXISTS and failed does not count, and that is the whole point — see
  // ArtworkOutcome.
  const visible = capsuleWentOn || !game.artwork?.[visibleSlot];

  const images = await background;
  for (let i = 0; i < BACKGROUND_SLOTS.length; i++) {
    const [slot, assetType] = BACKGROUND_SLOTS[i];
    if (images[i]) {
      await applySlot(images[i], assetType);
      continue;
    }
    // Nothing to apply. There are two ways to get here and they want opposite
    // things, so the reason matters rather than just the empty result:
    //
    //   the download failed  — the slot HAS art and we could not get it this
    //                          time. Leave what Steam already has; clearing on
    //                          a flaky network would wipe good artwork.
    //   nothing is offered   — the gateway says this slot is empty, so Steam's
    //                          copy is stale and has to go.
    //
    // The second case is why this exists. Website tiles used to carry our
    // cloud as their logo, and Steam draws the logo INSTEAD OF the title —
    // so every added page showed the same mark where its name should be. The
    // gateway stopped serving it, but a slot that is merely skipped keeps
    // whatever was written last time, and those tiles would have kept the
    // cloud for as long as they existed.
    if (!game.artwork?.[slot]) await clearSlot(appId, assetType);
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
  // A dedicated icon first, then the logo, then the capsule.
  //
  // It used to take the logo and nothing else, because the logo is closest to
  // an icon — small, transparent, centred — and was already downloaded. That
  // tied two unrelated things together: the only way to give a tile an icon
  // was to give it a logo, and Steam draws a logo INSTEAD OF the title. Every
  // website tile ended up showing the same mark where its name should be.
  //
  // The gateway now serves a square `icon` for those tiles and no logo at all,
  // so the two can finally differ. The fallbacks stay for everything else: a
  // real game has a publisher logo and no icon, and should keep using it.
  const logoIndex = BACKGROUND_SLOTS.findIndex(([slot]) => slot === "logo");
  const iconImage = (await iconFetch)
    ?? (logoIndex >= 0 ? images[logoIndex] : undefined)
    ?? capsule;

  // A page you added gets a logo drawn from its own name.
  //
  // Only for `custom`: the published page has a real wordmark and that one IS
  // its name. Every custom page was being given that same wordmark, so a shelf
  // of them all announced themselves as Shelfcloud — and since Steam draws the
  // logo INSTEAD of the title, the one thing saying which page you were
  // looking at was the thing being covered.
  //
  // Applied after the others so a real logo, if the gateway ever serves one
  // for this tile, always wins over a drawn one.
  if (game.provider === "custom" && !images[logoIndex]) {
    const drawn = await renderPageLogo(game.name, iconImage);
    if (drawn) await applySlot(drawn, LibraryAsset.Logo);
  }
  if (iconImage) {
    try {
      // appId as well as game_id: the file is named after the SHORTCUT now,
      // because it lands in Steam's own grid folder where `<id>.png` is
      // already Steam's library art for the app with that id.
      const saved = await saveIcon(game.game_id, iconImage.data, iconImage.kind, appId);
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

/** Make the tracked map agree with the shortcuts Steam actually has.
 *
 *  Two repairs, both invisible to reconcile on its own:
 *
 *  A tile that exists but is not tracked gets ADOPTED — recorded against the
 *  game it already names — instead of being left for the main loop to
 *  duplicate. Its artwork fingerprint is left empty so the next pass dresses
 *  it, since nothing here knows what art it carries.
 *
 *  A game with more than one tile keeps one and the rest are removed. The
 *  keeper is the already-tracked tile where there is one, otherwise the oldest,
 *  because that is the one likelier to hold playtime and a controller layout.
 *
 *  Never fatal. This is a repair, and a plugin that refuses to sync because it
 *  could not read a Steam file is worse than one with a duplicate tile.
 */
async function adoptStrayShortcuts(
  tracked: Record<string, TrackedShortcut>,
  callbacks: SyncCallbacks,
): Promise<void> {
  let report;
  try {
    report = await strayShortcuts();
  } catch {
    return;
  }
  if (!report?.ok || !Array.isArray(report.shortcuts)) return;

  for (const entry of report.shortcuts) {
    // Per ROW, not just the envelope. This runs before the main loop's
    // filtering, so one malformed row here threw out of the whole sync and
    // cost the user every tile — the same rule the backend already applies to
    // the library: one unusable entry is skipped, it does not stop the rest.
    if (!entry || typeof entry !== "object"
        || typeof entry.game_id !== "number"
        || typeof entry.keep !== "number") continue;

    const key = String(entry.game_id);
    const extras = (Array.isArray(entry.app_ids) ? entry.app_ids : [])
      .filter((id) => typeof id === "number" && id !== entry.keep);

    for (const appId of extras) {
      try {
        await clearArtwork(appId);
        await SteamClient.Apps.RemoveShortcut(appId);
        console.warn(`[shelfcloud] removed a duplicate tile for game ${entry.game_id}`);
      } catch {
        /* Steam kept it; the next sync will try again */
      }
    }

    const known = tracked[key];
    if (known && known.app_id === entry.keep) continue;     // already agreed

    // Either untracked, or tracked against a tile that is now gone. Adopt the
    // survivor so the main loop updates it rather than adding another.
    const adopted: TrackedShortcut = {
      app_id: entry.keep,
      name: known?.name ?? "",
      launch: known?.launch ?? "",
      art: "",            // unknown; let the next pass re-apply artwork
    };
    tracked[key] = adopted;
    try {
      // No DesiredGame here — this is reconciling a tile Steam already had.
      // The empty url leaves any fallback already recorded untouched; the
      // next full sync fills it in from the gateway.
      await callbacks.record(entry.game_id, entry.keep, adopted.name,
                             adopted.launch, "", "", "");
    } catch {
      /* the in-memory map is already right; the file catches up next time */
    }
  }
}

export interface SyncCallbacks {
  record: (
    gameId: number, appId: number, name: string, options: string, art: string,
    provider: string, url: string,
  ) => Promise<unknown>;
  forget: (gameId: number) => Promise<unknown>;
  onProgress?: (message: string) => void;
  /** Asked before each tile. True stops the run where it stands.
   *
   *  Checked BETWEEN tiles, never inside one: a tile is a shortcut, its
   *  artwork and its collection entry, and stopping half way through would
   *  leave a nameless entry with no art that nothing tracks. Whole tiles only,
   *  so whatever was done stays correct and the rest is simply not started. */
  shouldStop?: () => boolean;
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
 * 5 -> 6: the icon became its own slot instead of being taken from the logo.
 *         Website tiles lost their logo so their name would show, which also
 *         took their icon with it — those tiles are already on a shelf and
 *         would never be revisited without this.
 * 6 -> 7: a repair again, not a new slot. fetchSlot returned early when a slot
 *         had no direct URL and so never tried the gateway, which is the only
 *         source for art the gateway serves off its own disk. Any tile that
 *         was recorded under revision 6 with a slot silently skipped is marked
 *         done and would never be looked at again.
 */
const ART_REVISION = "7";

/**
 * Bump when what this plugin DRAWS for a website tile changes.
 *
 * Deliberately separate from ART_REVISION. That one sits in every tile's
 * fingerprint, so moving it re-downloads artwork for the entire library —
 * hundreds of games, four slots each, several minutes of syncing — in order to
 * reach a handful of website tiles. This one is only mixed in for a website
 * tile, so it reaches exactly the tiles whose picture changed and leaves the
 * games alone.
 *
 * It is APPENDED to the first field rather than added as a new one. A new
 * field would change the shape of the string for every game as well, which is
 * the whole thing being avoided.
 *
 * 0 -> 1: the capsule is now drawn here with the page's name on it. The
 *         gateway still serves the same nameless capsule at the same URL, so
 *         nothing else in the fingerprint moved and every website tile already
 *         on a shelf would have kept the blank one.
 */
const WEBSITE_ART_REVISION = "w1";

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
  const drawn = WEBSITE_PROVIDERS.has(game.provider) ? WEBSITE_ART_REVISION : "";
  return [ART_REVISION + drawn, a.capsule ?? "", a.header ?? "", a.hero ?? "",
          a.logo ?? "", a.icon ?? ""].join("|");
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

  // Before anything is created, make what this plugin believes agree with what
  // Steam actually has.
  //
  // Reconcile below works from `tracked`, so a tile created but never recorded
  // is invisible to it and the loop cheerfully makes a second one for the same
  // game. Nothing ever removes either, because neither is tracked. shortcuts.vdf
  // is the only place that knows, and every tile of ours carries --game-id, so
  // it can say which game each belongs to.
  await adoptStrayShortcuts(tracked, callbacks);

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
  // Kept as well as the batches. Filing happens a batch at a time so tiles
  // appear while the sync runs, but deciding what does NOT belong in a
  // collection needs the whole answer — a slice cannot tell the difference
  // between "filed elsewhere" and "not reached yet".
  const filed: CollectionMember[] = [];

  const flushCollections = async () => {
    if (!pending.length) return;
    const batch = pending;
    pending = [];
    await addToProviderCollections(batch);
  };

  const file = (member: CollectionMember) => {
    pending.push(member);
    filed.push(member);
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
  const MAX_NAME_LEN = 22;
  const shorten = (name: string) => (name.length > MAX_NAME_LEN ? `${name.slice(0, MAX_NAME_LEN - 1)}…` : name);
  const step = (label: string, name: string) => {
    done++;
    callbacks.onProgress?.(`${label} ${shorten(name)} (${done}/${total})`);
  };

  // Web pages go first, and a cancel never reaches them.
  //
  // Cancelling "add all my games" should drop the bulk it is still working
  // through, not whatever the loop had not got to yet. The published
  // Shelfcloud page is pushed to every account and the custom ones were each
  // typed in by hand — a handful of tiles, every one of them deliberate — so
  // losing them to a cancel aimed at three hundred owned games is the loop's
  // ordering showing through as behaviour.
  //
  // Ordering alone is not enough: a page added to the library AFTER a sync has
  // already started would still sit behind the cancel. So they are also exempt
  // from the check rather than merely early in it.
  const ordered = [
    ...desired.filter((g) => WEBSITE_PROVIDERS.has(g.provider)),
    ...desired.filter((g) => !WEBSITE_PROVIDERS.has(g.provider)),
  ];

  for (const game of ordered) {
    const mandatory = WEBSITE_PROVIDERS.has(game.provider);
    if (!mandatory && callbacks.shouldStop?.()) {
      result.cancelled = true;
      break;
    }
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

        if (game.controller_layout) {
          try {
            (SteamClient as any).Input.SetSelectedConfigForApp(
              appId, 0, game.controller_layout, false, 0,
            );
          } catch {
            /* not fatal */
          }
        }

        // Record the fingerprint only once the VISIBLE slot went on. It used to
        // be stored whenever any slot did, so a game whose capsule failed but
        // whose hero and logo succeeded counted as done — a blank tile with a
        // fully dressed detail page, never retried.
        const appliedNew = await applyArtwork(appId, game);
        if (appliedNew.applied === 0) artUnavailable.add(game.game_id);
        await callbacks.record(game.game_id, appId, title, game.options,
                               appliedNew.visible ? art : "", game.provider,
                               game.url ?? "");
        file({ appId, provider: game.provider });
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
                                 recordedArt, game.provider, game.url ?? "");
          result.updated++;
        }
        file({ appId: existing.app_id, provider: game.provider });
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
      }
    } catch (error) {
      console.error("[shelfcloud] sync failed for", game.name, error);
      result.failed++;
    }
  }

  for (const [gameId, entry] of Object.entries(tracked)) {
    if (desiredIds.has(Number(gameId))) continue;
    // Removals are cancellable too. The check used to be on the ADD loop
    // alone, which made Cancel do nothing at all during "Remove all games":
    // that run passes an empty desired list, so the add loop has nothing to
    // do and execution falls straight into this one. The button said
    // "Stopping…" and every remaining tile was deleted anyway.
    if (callbacks.shouldStop?.()) {
      result.cancelled = true;
      break;
    }
    try {
      step("Removing", entry.name);
      try {
        await clearArtwork(entry.app_id);
      } catch { /* best effort — the shortcut must still go */ }
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

  // And take out what no longer belongs — but NOT after a cancel.
  //
  // Pruning asks each collection "what should be in you" and answers from
  // `filed`, which is why it must see the whole sync: a slice cannot tell
  // "filed elsewhere" from "not reached yet". A cancelled run is exactly a
  // slice, so pruning on one would strip every tile the loop never got to out
  // of its collection — a cancel quietly un-sorting the library it had already
  // sorted. Leaving it for the next complete run costs nothing: an unpruned
  // collection holds a stale entry, which is what it held a moment ago anyway.
  if (!result.cancelled) {
    // Managed means "filed by this sync OR tracked before it" — the second
    // half matters, because a game dropped from the library is not filed
    // anywhere now and would otherwise be left sitting in its old collection
    // forever. Apps outside that set are the user's own doing, untouched.
    const managed = new Set<number>(filed.map((m) => m.appId));
    for (const entry of Object.values(tracked)) managed.add(entry.app_id);
    await pruneProviderCollections(filed, managed);
    // Last, and only on a complete run for the same reason the prune is: a
    // collection this plugin made under a label it no longer uses, such as
    // "custom (Shelfcloud)" from before website tiles were labelled
    // "Websites". The games in it have just been filed under the current name
    // by the lines above, so what goes is an empty grouping nobody asked for.
    await removeRetiredCollections(managed);
  }

  return result;
}
