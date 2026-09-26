// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Making the Steam library match what the user picked on the web.
//
// This lives on the frontend because only here does SteamClient exist. The
// backend decides *what* the library should contain; this makes it so.

import { fetchArtwork, installControllerLayout, logNote, saveIcon, saveIconFromUrl, setSteamIds, strayShortcuts } from "./api";
import { addToProviderCollections, pruneProviderCollections,
         removeRetiredCollections, type CollectionMember } from "./collection";
import { renderPageCapsule } from "./pagecapsule";
import { BRAND_MARK } from "./brandmark";
import { PER_GAME_TIMEOUT, RPC_TIMEOUT, steamCall, withTimeout } from "./withTimeout";
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

/**
 * How a tile is identified in the plugin's own record.
 *
 * The backend sends this as `key`; it is recomputed here only for an older
 * backend that does not, so a version skew degrades to the previous
 * one-tile-per-game behaviour instead of mis-keying everything.
 */
export function trackingKey(game: DesiredGame): string {
  return game.key ?? `${game.game_id}:${game.provider}`;
}

/**
 * The record for this tile, tolerating one written before the split.
 *
 * The backend re-keys synced.json on load, but it deliberately leaves alone
 * any entry that recorded no provider: it cannot know which service such a
 * tile opens, and a guessed key would make the tile match nothing desired and
 * be deleted, taking its playtime and artwork with it. Those are matched here
 * by game id instead, and the next write gives them the current shape.
 */
function trackedFor(
  tracked: Record<string, TrackedShortcut>, game: DesiredGame,
): TrackedShortcut | undefined {
  const exact = tracked[trackingKey(game)];
  if (exact) return exact;
  const legacy = tracked[String(game.game_id)];
  // Only if it names no provider. One that does belongs to a different tile.
  return legacy && !legacy.provider ? legacy : undefined;
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
/** Say which phase the sync is in, in the log file on disk.
 *
 *  Every freeze here has been diagnosed by inference: the log went quiet and
 *  the last line was whatever happened to be logged for some other reason.
 *  Three fixes were aimed at the wrong await that way. The phases are named
 *  now, so a stall says where it is instead of leaving it to be guessed.
 *
 *  Bounded itself: reporting progress must not become another thing that can
 *  hang the sync.
 */
async function phase(name: string): Promise<void> {
  await withTimeout(logNote(`phase: ${name}`), undefined, 5000);
}

/** An image the backend fetched, and the address it came from.
 *
 *  The URL is carried alongside the bytes so the ICON can be saved by
 *  reference. Every other slot is applied through Steam and needs the bytes;
 *  the icon has to become a FILE, and pushing the picture back over the socket
 *  to do that is what froze the sync -- see saveIconFromUrl.
 *
 *  Optional because an image DRAWN here has no address to name. Those go back
 *  as bytes, which is safe for the same reason it was never safe for artwork:
 *  a drawn capsule is a few kilobytes of flat colour and text, not a 600x900
 *  photograph off a CDN.
 */
type FetchedImage = { data: string; kind: "png" | "jpg"; url?: string };

async function fetchArtworkBase64(url: string): Promise<FetchedImage | undefined> {
  try {
    // Collected in slices, because one message cannot carry a whole image.
    //
    // Decky's RPC replies ride a websocket, and aiohttp CLOSES the connection
    // on a message over 4 MB rather than erroring. Steam hero art is 6-7 MB,
    // which is 9 MB of base64 -- so the first hero of every sync killed the
    // socket, and every call after it waited for an answer that could never
    // arrive. That is the freeze, and the reason it landed on a different
    // game each run: whichever one happened to need artwork first.
    let result = await withTimeout(fetchArtwork(url), undefined, RPC_TIMEOUT);
    if (!result?.ok || !result.base64) return undefined;

    let data = result.base64;
    const kind = result.kind === "png" ? "png" : "jpg";
    const total = result.total ?? data.length;
    // Bounded by the data itself, not by trust: a backend that kept answering
    // with an empty slice would otherwise spin here for ever.
    while (data.length < total) {
      const more = await withTimeout(
        fetchArtwork(url, data.length), undefined, RPC_TIMEOUT);
      if (!more?.ok || !more.base64) return undefined;
      data += more.base64;
    }
    return { data, kind, url };
  } catch {
    return undefined;
  }
}

type ArtSlot = "capsule" | "header" | "hero" | "logo" | "icon" | "cover";

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
    await steamCall(SteamClient.Apps.ClearCustomArtworkForApp(appId, assetType as never));
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

/* Steam's own globals, which @decky/ui does not type.
 *
 * Declared rather than reached for through `any`, the same way collection.ts
 * declares the collection store. `any` turns a rename inside Steam into a
 * silent undefined at runtime; a declared shape at least states what this
 * code believes, and every member is optional because all of it is Steam's to
 * change without notice. */
interface SteamController {
  eControllerType?: number;
  nControllerIndex?: number;
}

interface ControllerStore {
  GetControllers?(): SteamController[];
}

interface SteamInput {
  SetSelectedConfigForApp?(
    appId: number, controllerIndex: number, templateUrl: string,
    unknownFlag: boolean, selection: number,
  ): void;
}

interface SteamWindow {
  ControllerStore?: ControllerStore;
  controllerStore?: ControllerStore;
  appStore?: { GetAppOverviewByAppID?(appId: number): unknown };
}

/* Looked up on each call, never captured at module load. `window` does not
 * exist when these modules are imported under a test runner, and a top-level
 * reference to it made importing sync.ts throw — taking six test files down
 * with it. */
function steam(): SteamWindow {
  return (typeof window === "undefined" ? {} : window) as unknown as SteamWindow;
}

/**
 * Find the Deck's built-in controller index (type 4 = SteamControllerNeptune).
 * Returns null if not found. moonlight-steam-sync-decky probe confirmed
 * the index is NOT always 0.
 */
function deckControllerIndex(): number | null {
  try {
    const store = steam().ControllerStore ?? steam().controllerStore;
    const controllers: SteamController[] = store?.GetControllers?.() ?? [];
    for (const c of controllers) {
      if (c?.eControllerType === 4) return c.nControllerIndex ?? 0;
    }
    return controllers.length === 1 ? controllers[0]?.nControllerIndex ?? 0 : 0;
  } catch { return 0; }
}

/**
 * Wait for Steam to know this app exists, then set its controller config.
 * moonlight-steam-sync probe V2: 5th arg must be 1 (CONFIG_SELECTION_USER),
 * and the app must be in appStore before the call sticks.
 */
function applyControllerConfig(
  appId: number, templateUrl: string, waitForStore: boolean,
): Promise<void> {
  return new Promise<void>((resolve) => {
    const input = (SteamClient as unknown as { Input?: SteamInput }).Input;
    const set = input?.SetSelectedConfigForApp;
    if (typeof set !== "function") { resolve(); return; }

    const idx = deckControllerIndex() ?? 0;
    const inStore = () => !!steam().appStore?.GetAppOverviewByAppID?.(appId);

    const apply = () => {
      try { set.call(input, appId, idx, templateUrl, false, 1); }
      catch { /* swallow */ }
      resolve();
    };

    // A tile that ALREADY existed is either in appStore now or is never going
    // to be, so it gets one look and no waiting. Polling it was costing five
    // seconds of every sync: one tracked tile ("Decimated (Boosteroid)") had
    // been removed from shortcuts.vdf behind the plugin's back, so its appid
    // resolved to nothing, and the loop is serial — ten attempts at 500 ms
    // stalled all 387 tiles behind it.
    if (!waitForStore) {
      if (inStore()) apply(); else resolve();
      return;
    }

    // A tile created moments ago is different: appStore genuinely lags
    // AddShortcut, and without the wait the call lands before Steam knows the
    // app and silently selects nothing.
    const tryApply = (attempt: number) => {
      if (inStore() || attempt >= 10) apply();
      else setTimeout(() => tryApply(attempt + 1), 500);
    };
    tryApply(0);
  });
}

async function applyArtwork(appId: number, game: DesiredGame): Promise<ArtworkOutcome> {
  let applied = 0;

  const applySlot = async (
    image: Awaited<ReturnType<typeof fetchSlot>>,
    assetType: number,
  ) => {
    if (!image) return false;
    try {
      // The call that froze two syncs. It is wrapped rather than merely
      // caught because it did not fail -- it never came back, and the serial
      // loop behind it stopped with it.
      await steamCall(
        SteamClient.Apps.SetCustomArtworkForApp(appId, image.data, image.kind, assetType as never));
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
  } else if (!capsule) {
    // A GAME with no capsule, drawn from its own picture.
    //
    // 79 catalogue games have a cover and no capsule — Luna's smaller titles,
    // a few Boosteroid demos. The slot stayed empty, so the tile kept the
    // gateway's website picture and eighteen different games in one library
    // were the same blue cloud. Searching for any of them looked like the
    // same game listed over and over.
    //
    // The same drawing a website tile gets, for the same reason and with the
    // same result: the game's own art, centred, with its name underneath. The
    // cover is wide and the capsule is a tall portrait, so composing it beats
    // stretching it — and a real picture with a real name beats a cloud.
    const cover = await fetchSlot(game, "cover");
    if (cover) {
      const drawn = await renderPageCapsule(game.name, cover);
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

  // Anything with no logo of its own gets one drawn from its name.
  //
  // Steam draws the logo INSTEAD OF the title, so an empty slot is a game
  // with nothing written over its hero and a WRONG slot is worse: filled with
  // the Shelfcloud wordmark, a game's page announced itself as "shelfcloud"
  // and the one thing saying which game you were looking at was the thing
  // covering it. Disney/Pixar WALL-E said shelfcloud.
  //
  // This used to be for `custom` pages only, on the reasoning that the
  // published page has a real wordmark and that one IS its name. True for
  // that page; not true for the several hundred cloud games whose publishers
  // ship no logo. They have the same problem and want the same answer: our
  // mark, and the name of the thing itself beside it.
  //
  // Applied after the others so a real publisher logo always wins over a
  // drawn one.
  if (!images[logoIndex]) {
    // BRAND_MARK, not iconImage. The mark beside the name is ours and is the
    // same on every tile; iconImage is a FALLBACK CHAIN for the shortcut icon
    // and lands wherever it lands. Once the icon slot began 404ing for
    // catalogue games -- correctly, a game should wear its own art -- that
    // chain fell through to the capsule, and a game with no capsule gets the
    // gateway's default tile, which is fully opaque. The name then sat beside
    // a black rectangle instead of a floating cloud.
    const drawn = await renderPageLogo(game.name, BRAND_MARK);
    if (drawn) await applySlot(drawn, LibraryAsset.Logo);
  }
  if (iconImage) {
    try {
      // appId as well as game_id: the file is named after the SHORTCUT now,
      // because it lands in Steam's own grid folder where `<id>.png` is
      // already Steam's library art for the app with that id.
      // By URL where there is one, which is every image that came off a CDN.
      //
      // Handing over the BYTES is what froze the sync. This was the only large
      // message travelling frontend -> backend and the only one never sliced,
      // while the same picture coming the other way has been chunked at 192 KB
      // all along: aiohttp CLOSES a websocket on a message over 4 MB instead
      // of raising, and a closed socket does not fail a call, it silences it.
      // Nothing settles, so not even the timeout can report it -- the log ends
      // mid-game with no error on either side.
      //
      // Nor was it a rare image that did it. The icon falls back to the
      // CAPSULE for any game shipping no icon of its own, and a capsule is
      // 600x900. Measured on hardware: the sync died on the FIRST game of
      // every run, right here, having logged every step before it.
      //
      // The backend downloaded this picture already -- it is what served the
      // slices -- so naming it costs a few hundred bytes and no second fetch.
      // Only a DRAWN image has no address, and those are kilobytes.
      const saved = await withTimeout(
        iconImage.url
          ? saveIconFromUrl(game.game_id, iconImage.url, appId)
          : saveIcon(game.game_id, iconImage.data, iconImage.kind, appId),
        undefined, RPC_TIMEOUT);
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
    await phase("scan stray shortcuts");
  report = await withTimeout(strayShortcuts(), undefined, RPC_TIMEOUT);
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

    // The backend groups by game AND service now, and sends the key it used.
    // Grouped by game alone, a game's Boosteroid, Luna and GeForce NOW tiles
    // are one group and everything but `keep` below is destroyed — so falling
    // back to the game id is only safe for an older backend, which never
    // produces more than one tile per game in the first place.
    const key = entry.key ?? String(entry.game_id);
    const extras = (Array.isArray(entry.app_ids) ? entry.app_ids : [])
      .filter((id) => typeof id === "number" && id !== entry.keep);

    for (const appId of extras) {
      try {
        await clearArtwork(appId);
        await steamCall(SteamClient.Apps.RemoveShortcut(appId));
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
      await withTimeout(
        // No DesiredGame here, so no Steam id to offer -- the backend keeps
        // whatever the tile already had rather than blanking it.
        callbacks.record(key, entry.keep, adopted.name,
                         adopted.launch, "", entry.provider ?? "", "", ""),
        undefined, RPC_TIMEOUT);
    } catch {
      /* the in-memory map is already right; the file catches up next time */
    }
  }
}

export interface SyncCallbacks {
  record: (
    // The tracking key, "<game id>:<provider>" — not a game id. The backend
    // still accepts a bare id from an older frontend and normalises it.
    key: string, appId: number, name: string, options: string, art: string,
    provider: string, url: string,
    /** The game's id on STEAM, so the tile can later be asked how many
     *  people are playing it. Empty for the games that are not on Steam. */
    steamAppId: string,
  ) => Promise<unknown>;
  forget: (key: string) => Promise<unknown>;
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
 * 7 -> 8: the drawn logo was floating. It was painted into a 640x360 canvas,
 *         the size a publisher uses when the artwork FILLS it, while ours only
 *         reached 71% of the height -- and Steam pins the logo to the bottom
 *         of the hero, so the empty part became a gap and the words sat high.
 *         The canvas is now cut to the content. Every tile already carries the
 *         floating version and nothing else about it changed, so without this
 *         they would keep it for as long as they exist.
 * 8 -> 9: the mark beside the name was coming from the shortcut icon's
 *         fallback chain, which now 404s for catalogue games and fell through
 *         to the capsule -- opaque for any game without one, so the name sat
 *         beside a black rectangle. The mark is compiled in now. Same reason
 *         as above: every tile already carries the black version.
 */
export const ART_REVISION = "9";

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
  // Keyed by game AND provider, because a game is not always one tile: with
  // sync_all_providers on it has one per service, they all carry the same
  // game_id, and a set of game ids cannot tell them apart — every tile past
  // the first would read as "not desired" and be deleted on the same sync
  // that created it.
  const desiredKeys = new Set(desired.map(trackingKey));

  // Before anything is created, make what this plugin believes agree with what
  // Steam actually has.
  //
  // Reconcile below works from `tracked`, so a tile created but never recorded
  // is invisible to it and the loop cheerfully makes a second one for the same
  // game. Nothing ever removes either, because neither is tracked. shortcuts.vdf
  // is the only place that knows, and every tile of ours carries --game-id, so
  // it can say which game each belongs to.
  await withTimeout(adoptStrayShortcuts(tracked, callbacks), undefined, RPC_TIMEOUT);

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
    await withTimeout(addToProviderCollections(batch), undefined, RPC_TIMEOUT);
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
  const toRemove = Object.keys(tracked).filter((key) => !desiredKeys.has(key)).length;
  const toChange = desired.filter((g) => {
    const existing = trackedFor(tracked, g);
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
  /** Say what is being looked at, WITHOUT a fraction.
   *
   *  The denominator counts games that need work, and deliberately so: making
   *  it every game walked put 308 on a panel whose button had offered 124,
   *  which reads as a bug even though both are right. That stays.
   *
   *  What did not stay true is the reason unchanged games were left silent --
   *  "hundreds pass instantly, there is no stall to hide". They do not pass
   *  instantly on a library of nine hundred: the scan is most of half a
   *  minute, and for all of it the panel showed one frozen line. After an
   *  afternoon where the plugin genuinely was stuck, a progress display that
   *  looks stuck is the one thing it must not do.
   *
   *  So the name moves and the fraction does not. Nothing to reconcile with
   *  the button, and it is visibly alive. */
  const scanning = (name: string) => {
    callbacks.onProgress?.(`Checking ${shorten(name)} (${done}/${total})`);
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

  await phase(`tiles (${ordered.length} desired)`);
  for (const game of ordered) {
    const mandatory = WEBSITE_PROVIDERS.has(game.provider);
    if (!mandatory && callbacks.shouldStop?.()) {
      result.cancelled = true;
      break;
    }
    const existing = trackedFor(tracked, game);
    const title = shortcutTitle(game);
    const art = artworkFingerprint(game);
    scanning(game.name);
    try {
      // The whole turn, under one deadline. See PER_GAME_TIMEOUT: the inner
      // calls are each bounded already, and the sync still froze, so this
      // stops depending on having guessed the right inner call.
      const finished = await withTimeout((async () => {
      if (!existing) {
        step("Adding", game.name);
        // 0 on timeout, which the check below already treats as a refusal.
        const appId = await withTimeout(SteamClient.Apps.AddShortcut(
          title, game.exe, START_DIR, game.options,
        ), 0);
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
            const layoutResult = await withTimeout(
              installControllerLayout(title, game.controller_layout), undefined, RPC_TIMEOUT);
            const liveUrl = layoutResult?.autosave_url ?? game.controller_layout;
            await applyControllerConfig(appId, liveUrl, true);
          } catch { /* not fatal */ }
        }

        // Record the fingerprint only once the VISIBLE slot went on. It used to
        // be stored whenever any slot did, so a game whose capsule failed but
        // whose hero and logo succeeded counted as done — a blank tile with a
        // fully dressed detail page, never retried.
        const appliedNew = await applyArtwork(appId, game);
        if (appliedNew.applied === 0) artUnavailable.add(game.game_id);
        await withTimeout(
          callbacks.record(trackingKey(game), appId, title, game.options,
                           appliedNew.visible ? art : "", game.provider,
                           game.url ?? "", String(game.steam_app_id ?? "")),
          undefined, RPC_TIMEOUT);
        file({ appId, provider: game.provider });
        result.added++;
        result.addedNames.push(game.name);
        // File this batch now, so the tiles are visible in their collection
        // while the rest of the sync is still running.
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
      } else {
        const launchChanged = existing.launch !== game.options || existing.name !== title;
        const artChanged = (existing.art ?? "") !== art;

        // Only when this tile is actually being touched.
        //
        // This ran for EVERY game with a layout, changed or not -- 891 backend
        // calls on a library this size, each one writing four files just to
        // find nothing needed writing. That is the bulk of a sync's traffic
        // spent confirming that nothing happened, and it put roughly two
        // thousand round trips through Decky's socket per run.
        //
        // A provider's layout DOES change sometimes, and an untouched tile
        // would miss it -- so that is what bumping ART_REVISION is for: it
        // makes artChanged true everywhere, every tile is touched once, and
        // the new layout goes on with the artwork.
        if (game.controller_layout && (launchChanged || artChanged)) {
          try {
            const layoutResult = await withTimeout(
              installControllerLayout(title, game.controller_layout), undefined, RPC_TIMEOUT);
            const liveUrl = layoutResult?.autosave_url ?? game.controller_layout;
            await applyControllerConfig(existing.app_id, liveUrl, false);
          } catch { /* best effort */ }
        }

        // Artwork is compared separately from the launch command: the gateway
        // resolves better art over time, and a tile whose command never
        // changes must still pick it up rather than staying blank forever.

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
          await withTimeout(
            callbacks.record(trackingKey(game), existing.app_id, title, game.options,
                             recordedArt, game.provider, game.url ?? "",
                             String(game.steam_app_id ?? "")),
            undefined, RPC_TIMEOUT);
          result.updated++;
        }
        file({ appId: existing.app_id, provider: game.provider });
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
      }
      return true;
      })(), false, PER_GAME_TIMEOUT);
      if (!finished) {
        // Named, because this is the only evidence of WHAT hangs. A run that
        // logs the same title every time is a bad tile; a run that names a
        // different one each time is something about the client, not the game.
        console.warn("[shelfcloud] gave up on", game.name, "after",
                     PER_GAME_TIMEOUT, "ms — moving on");
        // Into the plugin's log file, not just the console: this is the
        // only trace that outlives the sync it describes. Bounded itself --
        // reporting a hang must not hang.
        await withTimeout(logNote(`gave up on ${game.name} after ${PER_GAME_TIMEOUT}ms`),
                          undefined, 5000);
        result.failed++;
      }
    } catch (error) {
      console.error("[shelfcloud] sync failed for", game.name, error);
      result.failed++;
    }
  }

  await phase("remove unwanted tiles");
  for (const [key, entry] of Object.entries(tracked)) {
    if (desiredKeys.has(key)) continue;
    // A pre-split entry is keyed by game id alone and matches no desired key,
    // so it would be removed here as unwanted — and the tile it names is one
    // the sync just adopted and updated. Skip it while any desired tile still
    // claims that game; the record is re-keyed on the next write.
    if (!key.includes(":") && desired.some((g) => String(g.game_id) === key)) continue;
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
        await withTimeout(clearArtwork(entry.app_id), undefined, RPC_TIMEOUT);
      } catch { /* best effort — the shortcut must still go */ }
      SteamClient.Apps.RemoveShortcut(entry.app_id);
      await withTimeout(callbacks.forget(key), undefined, RPC_TIMEOUT);
      result.removed++;
    } catch (error) {
      console.error("[shelfcloud] could not remove", entry.name, error);
      result.failed++;
    }
  }

  // Whatever did not fill a batch. Best effort — unsorted tiles still play.
  // Backfill the Steam appids, once, for every tile the library knows.
  //
  // record_shortcut carries this too, but only for tiles a sync TOUCHES --
  // and on a settled library that is none of them, so the field stayed empty
  // on all 890 and the player badge had nothing to ask Steam about. One call
  // with the whole map reaches them all without re-applying any artwork.
  const steamIds: Record<string, string> = {};
  for (const game of ordered) {
    const id = game.steam_app_id;
    // Only games that ARE on Steam. About one in six is not, and those get
    // no entry rather than an empty one.
    if (id !== undefined && id !== null && String(id)) {
      steamIds[trackingKey(game)] = String(id);
    }
  }
  if (Object.keys(steamIds).length) {
    await withTimeout(setSteamIds(steamIds), undefined, RPC_TIMEOUT);
  }

  await phase("flush collections");
  await withTimeout(flushCollections(), undefined, RPC_TIMEOUT);

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
    await phase("prune collections");
  await withTimeout(pruneProviderCollections(filed, managed), undefined, RPC_TIMEOUT);
    // Last, and only on a complete run for the same reason the prune is: a
    // collection this plugin made under a label it no longer uses, such as
    // "custom (Shelfcloud)" from before website tiles were labelled
    // "Websites". The games in it have just been filed under the current name
    // by the lines above, so what goes is an empty grouping nobody asked for.
    await phase("retire collections");
  await withTimeout(removeRetiredCollections(managed), undefined, RPC_TIMEOUT);
  await phase("done");
  }

  return result;
}
