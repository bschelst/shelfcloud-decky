// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Makes the Steam library match what the user picked on the web. Lives on the
// frontend because SteamClient only exists here; the backend decides what the
// library should hold, this makes it so.

import { fetchArtwork, installControllerLayout, layoutSelectionBase, logNote, saveIcon, saveIconFromUrl, setSetting, setSteamIds, strayShortcuts } from "./api";
import { addToProviderCollections, pruneProviderCollections,
         removeRetiredCollections, type CollectionMember } from "./collection";
import { layoutKey } from "./layoutKey";
import { renderPageCapsule } from "./pagecapsule";
import { BRAND_MARK } from "./brandmark";
import { PER_GAME_TIMEOUT, RPC_TIMEOUT, steamCall, withTimeout } from "./withTimeout";
import { renderPageLogo } from "./pagelogo";
import { providerLabel, WEBSITE_PROVIDERS, type DesiredGame, type SyncResult, type TrackedShortcut } from "./types";

// Which games a cancelled bulk add should hand back for removal. A game counts
// only if it is not in `before` (this run added it), not tracked (no tile made)
// and not a web page (web pages are never removed by a cancel). before === null
// means we could not read the library, so remove nothing instead of guessing.
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

// How a tile is named in our own record. The backend sends `key`; we only build
// it here for an older backend that does not, so a version mismatch falls back
// to one tile per game instead of mis-keying everything.
export function trackingKey(game: DesiredGame): string {
  return game.key ?? `${game.game_id}:${game.provider}`;
}

// The record for this tile. Also handles an old entry that stored no provider:
// the backend leaves those alone (it can't know which service they open), so we
// match them by game id here and the next write gives them the new shape.
function trackedFor(
  tracked: Record<string, TrackedShortcut>, game: DesiredGame,
): TrackedShortcut | undefined {
  const exact = tracked[trackingKey(game)];
  if (exact) return exact;
  const legacy = tracked[String(game.game_id)];
  // only if it names no provider. one that does belongs to an other tile.
  return legacy && !legacy.provider ? legacy : undefined;
}

export function shortcutTitle(game: DesiredGame): string {
  // a web page gets no suffix. the suffix tells a streamed copy apart from an
  // installed one of the same name, and a page has no installed copy to confuse
  // it with, so it would only add noise: "Shelfcloud Website (shelfcloud)".
  if (WEBSITE_PROVIDERS.has(game.provider)) return game.name;
  return `${game.name} (${providerLabel(game.provider)})`;
}


// Steam library asset slots. Declared here because @decky/ui does not re-export
// ELibraryAssetType; these are the enum's values.
const LibraryAsset = { Capsule: 0, Hero: 1, Logo: 2, Header: 3, Icon: 4 } as const;

const START_DIR = "/usr/bin/";

// Games whose artwork could not be applied this session. Retrying a failed slot
// is fine, but not every 60 seconds forever: some games have no artwork anywhere
// and were "updated" on every sync. Per session, so a reload gives one more try.
const artUnavailable = new Set<number>();

// Games we drew a fallback logo for, after the real one would not download.
// This bounds the logo self-heal: the first miss keeps the old fingerprint so
// the next sync tries the real logo one more time, but a url that simply never
// resolves (there are a couple hundred dead steam cdn logos) is then accepted
// instead of re-applying all its artwork on every sync forever -- that showed
// up as "8 artworks updated", agian and again.
const logoRetried = new Set<number>();

// Tiles whose controller layout is already selected this session. Selecting is
// two Steam round trips, and doing it for every tile on every sync turned a run
// that changes nothing into a full minute of work (measured 61s for 844 tiles).
// Steam keeps the selection once made, so the second pass has nothing to do.
const layoutSelected = new Set<number>();

// Write which phase the sync is in to the log file. Every freeze here was found
// by guessing from where the log went quiet, so name the phases and a stall says
// where it is. Bounded, so reporting progress can't itself hang the sync.
async function phase(name: string): Promise<void> {
  await withTimeout(logNote(`phase: ${name}`), undefined, 5000);
}

// An image the backend fetched, plus its address. The url lets the ICON be saved
// by reference; pushing its bytes back over the socket is what froze the sync (see
// saveIconFromUrl). Optional because a DRAWN image has no address, and those small
// ones go back as bytes safely.
type FetchedImage = { data: string; kind: "png" | "jpg"; url?: string };

async function fetchArtworkBase64(url: string): Promise<FetchedImage | undefined> {
  try {
    // Collected in slices, one message can't carry a whole image. Decky's RPC
    // rides a websocket and aiohttp CLOSES it on a message over 4 MB instead of
    // erroring. Steam hero art is 6-7 MB (9 MB of base64), so the first hero of a
    // sync killed the socket and every later call waited forever. That was the
    // freeze, landing on a different game each run.
    let result = await withTimeout(fetchArtwork(url), undefined, RPC_TIMEOUT);
    if (!result?.ok || !result.base64) return undefined;

    let data = result.base64;
    const kind = result.kind === "png" ? "png" : "jpg";
    const total = result.total ?? data.length;
    // bounded by the data, not by trust: a backend answering with empty slices
    // would otherwise spin here forever.
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

// The slot shown on the tile itself, and the ones behind it. Capsule is the only
// one visible in the library grid. Header is the wide banner Steam shows for the
// selected game; setting only the capsule leaves that panel blank, which reads as
// broken artwork. Hero and logo back the detail page.
const VISIBLE_SLOT: [ArtSlot, number] = ["capsule", LibraryAsset.Capsule];
const BACKGROUND_SLOTS: Array<[ArtSlot, number]> = [
  ["header", LibraryAsset.Header],
  ["hero", LibraryAsset.Hero],
  ["logo", LibraryAsset.Logo],
];

// Download one slot's image, preferring the direct URL over the gateway. Steam's
// own CDN sends the CORS header so most art needs no detour; SteamGridDB sends
// none, and this runs inside Steam's client, so the browser throws that response
// away. The gateway can serve art whose host is blocked, but it is an extra hop,
// so it is the fallback.
async function fetchSlot(game: DesiredGame, slot: ArtSlot) {
  const direct = game.artwork?.[slot];
  const viaGateway = game.artwork_proxy?.[slot];

  // No direct URL is not the same as no artwork. The gateway serves some slots
  // from its own disk (website tiles are the whole of that case), so the row can
  // carry a null capsule while the gateway still answers. A slot with neither is
  // empty, which is normal: website tiles have no logo, so Steam draws the name.
  if (direct) {
    const image = await fetchArtworkBase64(direct);
    if (image) return image;
  }
  return viaGateway ? fetchArtworkBase64(viaGateway) : undefined;
}

// What came of one game's artwork. `applied` counts every slot that went on;
// `visible` is narrower and decides whether we are finished: did the CAPSULE, the
// only slot in the grid, get its picture? A count can't tell them apart, so a
// capsule that failed while hero+logo went on read as success and a blank grid
// tile got a fully dressed detail page ("007 First Light").
interface ArtworkOutcome {
  applied: number;
  visible: boolean;
  // Did the real logo go on? A game whose logo download failed gets a drawn
  // fallback that must not be recorded as done. False means "retry next sync".
  logoSettled: boolean;
}

// Take a tile's artwork with it when the tile goes. Steam keys custom artwork to
// the app id and never tidies it, so removing a shortcut leaves four files behind
// forever. Measured on a real Deck: 4.3 GB of grid files, most of it orphaned,
// enough to break other plugins that enumerate the folder. Best effort per slot,
// a slot that fails to clear must not stop the removal.
async function clearSlot(appId: number, assetType: number): Promise<void> {
  try {
    await steamCall(SteamClient.Apps.ClearCustomArtworkForApp(appId, assetType as never));
  } catch {
    /* already gone, or Steam refused this slot; the others still mater */
  }
}

async function clearArtwork(appId: number): Promise<void> {
  // Capsule, Hero, Logo, Header — the four we write. Icon is a file path set
  // through SetShortcutIcon and is cleaned up by the backend.
  for (const assetType of [0, 1, 2, 3]) {
    await clearSlot(appId, assetType);
  }
}

// Steam's own globals, which @decky/ui does not type. Declared instead of `any`
// so a rename inside Steam shows up; every member optional since it is Steam's.
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
  // What Steam currently has selected, and how. eSelectionType 1 is a USER
  // selection, the one thing we must not overwrite. URL is the config's own
  // address, which is how our layout is told apart from a hand-picked one.
  GetConfigForAppAndController?(
    appId: number, controllerIndex: number,
  ): Promise<{ URL?: string; eSelectionType?: number; Title?: string }>;
}

interface SteamWindow {
  ControllerStore?: ControllerStore;
  controllerStore?: ControllerStore;
  appStore?: { GetAppOverviewByAppID?(appId: number): unknown };
}

// Looked up on each call, never at module load: `window` does not exist under the
// test runner, and a top-level reference made importing sync.ts throw.
function steam(): SteamWindow {
  return (typeof window === "undefined" ? {} : window) as unknown as SteamWindow;
}

// Find the Deck's built-in controller index (type 4 = SteamControllerNeptune).
// Returns null if not found. A moonlight-steam-sync-decky probe showed the index
// is not always 0.
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

// Wait for Steam to know this app exists, then set its controller config. The
// app must be in appStore before the call sticks.
function applyControllerConfig(
  appId: number, templateUrl: string, waitForStore: boolean,
  force = false,
): Promise<void> {
  return new Promise<void>((resolve) => {
    const input = (SteamClient as unknown as { Input?: SteamInput }).Input;
    const set = input?.SetSelectedConfigForApp;
    if (typeof set !== "function") { resolve(); return; }

    const idx = deckControllerIndex() ?? 0;
    const inStore = () => !!steam().appStore?.GetAppOverviewByAppID?.(appId);

    const apply = () => {
      // Never overwrite a layout the USER chose. The selection runs for every
      // tile on every sync now, so without this it would quietly undo someone's
      // own choice. eSelectionType 1 is a user selection, left alone unless it is
      // already OURS. Best effort: if the getter throws we set it as before.
      const ours = templateUrl.toLowerCase();
      void (async () => {
        try {
          const get = input?.GetConfigForAppAndController;
          if (!force && typeof get === "function") {
            const current = await get.call(input, appId, idx);
            const url = String(current?.URL ?? "");
            if (current?.eSelectionType === 1 && url && url.toLowerCase() !== ours) {
              resolve();
              return;
            }
          }
        } catch { /* fall through and set it */ }
        // 0, not 1. This one argument was the whole bug. An old note said the 5th
        // argument "must be 1", but Steam's own read-back says the opposite: sel 1
        // records a USER selection naming an autosave Steam never wrote, so it
        // falls back to a default template for every provider equally.
        try { set.call(input, appId, idx, templateUrl, false, 0); }
        catch { /* swallow */ }
        resolve();
      })();
    };

    // A tile that ALREADY existed is either in appStore now or never will be, so
    // one look, no waiting. Polling it cost five seconds of every sync when a
    // tracked tile had been removed from shortcuts.vdf behind our back, stalling
    // every tile behind it in the serial loop.
    if (!waitForStore) {
      if (inStore()) apply(); else resolve();
      return;
    }

    // A tile created moments ago is different: appStore genuinely lags
    // AddShortcut, and without the wait the call lands before Steam knows the app
    // and selects nothing.
    const tryApply = (attempt: number) => {
      if (inStore() || attempt >= 10) apply();
      else setTimeout(() => tryApply(attempt + 1), 500);
    };
    tryApply(0);
  });
}

async function applyArtwork(
  appId: number, game: DesiredGame,
  turn: { abandoned: boolean } = { abandoned: false },
): Promise<ArtworkOutcome> {
  let applied = 0;

  const applySlot = async (
    image: Awaited<ReturnType<typeof fetchSlot>>,
    assetType: number,
  ) => {
    if (!image) return false;
    try {
      // The call that froze two syncs: it did not fail, it never came back.
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

  // Started before the capsule is awaited, so these download during it.
  const background = Promise.all(BACKGROUND_SLOTS.map(([slot]) => fetchSlot(game, slot)));
  // Downloads with the others but NOT in BACKGROUND_SLOTS: that list goes to Steam
  // as library assets, and an icon is a file path, applied further down.
  const iconFetch = fetchSlot(game, "icon");

  const capsule = await fetchSlot(game, visibleSlot);

  // A website tile's capsule is drawn here with its own name on it. The gateway
  // ships ONE nameless capsule for all of them, so a shelf is a wall of identical
  // squares. Drawn from the ICON (the 256px square mark), not the capsule, which
  // would squash its centred cloud. Without an icon the gateway's capsule stays.
  let visibleImage = capsule;
  if (WEBSITE_PROVIDERS.has(game.provider)) {
    const mark = await iconFetch;
    if (mark) {
      const drawn = await renderPageCapsule(game.name, mark);
      if (drawn) visibleImage = drawn;
    }
  } else if (!capsule) {
    // A GAME with no capsule, drawn from its own picture. 79 catalogue games have
    // a cover and no capsule, so the tile kept the gateway's cloud and a shelf of
    // different games all looked the same. Same drawing a website tile gets: the
    // game's art, centred, with its name under it. The cover is wide and the
    // capsule is a tall portrait, so composing beats stretching.
    const cover = await fetchSlot(game, "cover");
    if (cover) {
      const drawn = await renderPageCapsule(game.name, cover);
      if (drawn) visibleImage = drawn;
    }
  }

  const capsuleWentOn = await applySlot(visibleImage, visibleType);

  // Whether the tile the user looks at got its picture. "No capsule to apply"
  // counts as settled: the gateway has no grid art for this game, so retrying
  // forever changes nothing. A capsule that EXISTS and failed does not count.
  const visible = capsuleWentOn || !game.artwork?.[visibleSlot];

  const images = await background;
  // If this turn was abandoned while the slices were in flight, stop here rather
  // than spending the live game's slots on a tile nobody waits for.
  if (turn.abandoned) return { applied, visible: false, logoSettled: false };
  for (let i = 0; i < BACKGROUND_SLOTS.length; i++) {
    const [slot, assetType] = BACKGROUND_SLOTS[i];
    if (images[i]) {
      if (turn.abandoned) break;
      await applySlot(images[i], assetType);
      continue;
    }
    // Nothing to apply, and the two ways to get here want opposite things. If the
    // download failed the slot HAS art we could not get, so leave what Steam has
    // (clearing on a flaky network would wipe good artwork). If nothing is offered
    // the slot is empty and Steam's copy is stale and must go. The second case is
    // why this exists: website tiles that once carried our cloud as their logo
    // would keep it forever, since a merely skipped slot keeps the last value.
    if (!game.artwork?.[slot]) await clearSlot(appId, assetType);
  }

  // The icon is a different mechanism. SetCustomArtworkForApp writes the four
  // library slots as bytes; an icon lives in shortcuts.vdf as a FILE PATH and
  // goes through SetShortcutIcon, so the bytes are written to disk by the backend
  // first. Prefer a dedicated icon, then the logo, then the capsule. It used to
  // take the logo only, which tied two things: giving a tile an icon meant giving
  // it a logo, and Steam draws a logo INSTEAD OF the title. Website tiles now get
  // a square `icon` and no logo, so the two can differ. Fallbacks stay for games.
  const logoIndex = BACKGROUND_SLOTS.findIndex(([slot]) => slot === "logo");
  const iconImage = (await iconFetch)
    ?? (logoIndex >= 0 ? images[logoIndex] : undefined)
    ?? capsule;

  // Anything with no logo of its own gets one drawn from its name. Steam draws
  // the logo INSTEAD OF the title, so the wrong slot means a game's page shows
  // "shelfcloud" over the one thing naming it (WALL-E did this). Applied after the
  // others so a real publisher logo always wins over a drawn one.
  const realLogo = !!images[logoIndex];
  if (!realLogo) {
    // BRAND_MARK, not iconImage. The mark beside the name is ours and the same on
    // every tile; iconImage is a FALLBACK CHAIN that, once the icon slot began
    // 404ing for catalogue games, fell through to the opaque capsule, so the name
    // sat beside a black rectangle.
    const drawn = await renderPageLogo(game.name, BRAND_MARK);
    if (drawn) await applySlot(drawn, LibraryAsset.Logo);
  }

  // A drawn fallback is a stand-in. When the gateway DOES offer a real logo and
  // the download just failed, recording the fingerprint as done would freeze the
  // fallback in and never retry (the GeForce NOW Hogwarts tile hit this). So it is
  // settled only when the real logo went on, or there was none to fetch.
  const logoSettled = realLogo || !game.artwork?.logo;
  if (iconImage) {
    try {
      // appId as well as game_id: the file is named after the SHORTCUT now, since
      // it lands in Steam's grid folder where `<id>.png` is already that id's
      // library art. By URL where there is one, which is every image off a CDN.
      //
      // Handing over the BYTES is what froze the sync: the only large message
      // going frontend -> backend and never sliced. aiohttp CLOSES the socket on
      // a message over 4 MB instead of raising, and a closed socket silences the
      // call rather than failing it. The icon falls back to the CAPSULE (600x900)
      // for any game with no icon, so the sync died on the FIRST game of a run.
      // Naming the picture the backend already has costs a few hundred bytes.
      const saved = await withTimeout(
        iconImage.url
          ? saveIconFromUrl(game.game_id, iconImage.url, appId)
          : saveIcon(game.game_id, iconImage.data, iconImage.kind, appId),
        undefined, RPC_TIMEOUT);
      if (saved?.ok && saved.path) {
        SteamClient.Apps.SetShortcutIcon(appId, saved.path);
        applied += 1;
      } else if (saved && !saved.ok) {
        // Said out loud, with the game's name. An icon that never appears with
        // nothing logged looks the same as one nobody tried to set.
        void logNote(`icon for ${game.name}: ${saved.error ?? "refused"}`);
      }
    } catch (error) {
      // Swallowed for the SYNC, not for the LOG: an icon must never cost the
      // artwork already on, but an empty catch makes missing icons unanswerable.
      void logNote(`icon for ${game.name} failed: ${String(error)}`);
    }
  }

  return { applied, visible, logoSettled };
}

/** May the logo be recorded as done? Yes when it settled, or when we already
 *  spent this game's one retry on it. Marks the retry spent as a side effect,
 *  so a logo that keeps failing settles for the fallback after a single retry
 *  rather than looping. */
function logoRecorded(outcome: ArtworkOutcome, game: DesiredGame): boolean {
  if (outcome.logoSettled) return true;
  if (logoRetried.has(game.game_id)) return true;
  logoRetried.add(game.game_id);
  return false;
}

// Make the tracked map agree with the shortcuts Steam has. Two repairs the main
// loop can't do: a tile that exists but is not tracked gets ADOPTED instead of
// duplicated (art fingerprint left empty so the next pass dresses it), and a game
// with more than one tile keeps one (tracked or oldest) and drops the rest.
async function adoptStrayShortcuts(
  tracked: Record<string, TrackedShortcut>,
  callbacks: SyncCallbacks,
  // Tripped when the caller stops waiting. This walks the whole library under one
  // 20s deadline, so being abandoned partway is ordinary and must stop it: it kept
  // writing into `tracked` while the tile loop was reading it. Fine to abandon,
  // must not keep changing shared state once nobody waits for the answer.
  abandoned: { abandoned: boolean } = { abandoned: false },
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
    // Per ROW, not just the envelope. This runs before the main loop's filtering,
    // so one bad row threw out of the whole sync and cost the user every tile.
    // One unusable entry is skipped, it does not stop the rest.
    if (!entry || typeof entry !== "object"
        || typeof entry.game_id !== "number"
        || typeof entry.keep !== "number") continue;

    // The backend groups by game AND service now and sends the key it used.
    // Grouped by game alone, a game's Boosteroid, Luna and GeForce NOW tiles are
    // one group and everything but `keep` is destroyed, so falling back to the
    // game id is only safe for an older backend that never makes more than one
    // tile per game.
    const key = entry.key ?? String(entry.game_id);
    const extras = (Array.isArray(entry.app_ids) ? entry.app_ids : [])
      .filter((id) => typeof id === "number" && id !== entry.keep);

    for (const appId of extras) {
      if (abandoned.abandoned) return;
      try {
        await clearArtwork(appId);
        await steamCall(SteamClient.Apps.RemoveShortcut(appId));
        console.warn(`[shelfcloud] removed a duplicate tile for game ${entry.game_id}`);
      } catch {
        /* Steam kept it; the next sync will try again */
      }
    }

    if (abandoned.abandoned) return;
    const known = tracked[key];
    if (known && known.app_id === entry.keep) continue;     // already agreed

    // Either untracked, or tracked against a tile that is now gone. Adopt the
    // survivor so the main loop updates it rather than adding another.
    const adopted: TrackedShortcut = {
      app_id: entry.keep,
      name: known?.name ?? "",
      launch: known?.launch ?? "",
      art: "",            // unknown; let the next pass re-apply artwork opnieuw
    };
    tracked[key] = adopted;
    try {
      // No DesiredGame here, so no Steam id to offer: the backend keeps whatever
      // the tile already had rather than blanking it. The next full sync fills
      // the artwork in from the gateway.
      await withTimeout(
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
    // The game's id on STEAM, so the tile can later be asked how many people are
    // playing it. Empty for games that are not on Steam.
    steamAppId: string,
  ) => Promise<unknown>;
  forget: (key: string) => Promise<unknown>;
  onProgress?: (message: string) => void;
  // Asked before each tile; true stops the run where it stands. Checked BETWEEN
  // tiles, never inside one: a tile is a shortcut, its artwork and its collection
  // entry, and stopping halfway would leave a nameless entry nothing tracks.
  shouldStop?: () => boolean;
}

// Bump when the SET of slots we write changes. The fingerprint is built from
// artwork URLs, so a new slot changes nothing about it and existing tiles are
// never revisited; bumping this invalidates every fingerprint once so the next
// sync re-applies artwork everywhere. History:
//   1->2 wrote the icon slot. 2->3 reverted it (bumped not back to 1, so tiles
//   damaged under 2 get repaired). 3->4 icons via SetShortcutIcon. 4->5 repair,
//   capsule-failed tiles were wrongly marked done. 5->6 icon became its own slot.
//   6->7 repair, fetchSlot skipped the gateway. 7->8 drawn logo floated too high.
//   8->9 name sat beside a black rectangle, mark compiled in now.
export const ART_REVISION = "9";

// Bump when what we DRAW for a website tile changes. Seperate from ART_REVISION,
// which sits in every fingerprint and would re-download the whole library; this
// is only mixed in for a website tile. Appended to the first field so it does not
// change the string shape for games. 0->1: capsule drawn with the page's name.
const WEBSITE_ART_REVISION = "w1";

// A fingerprint of the artwork a tile carries, stored alongside the shortcut so
// sync can tell when the gateway has better art and re-apply it. Without it,
// artwork is only applied at creation and games resolved later stay blank.
export function artworkFingerprint(game: DesiredGame): string {
  const a = game.artwork ?? {};
  const drawn = WEBSITE_PROVIDERS.has(game.provider) ? WEBSITE_ART_REVISION : "";
  return [ART_REVISION + drawn, a.capsule ?? "", a.header ?? "", a.hero ?? "",
          a.logo ?? "", a.icon ?? ""].join("|");
}

// Reconcile Steam shortcuts against the desired library, from the tracked
// key -> appId map: desired but untracked creates a tile, tracked but not desired
// deletes it, tracked and changed updates in place. Update instead of recreate so
// the tile keeps its playtime, controller layout and collections.
export async function syncLibrary(
  desired: DesiredGame[],
  tracked: Record<string, TrackedShortcut>,
  callbacks: SyncCallbacks,
): Promise<SyncResult> {
  const result: SyncResult = { added: 0, removed: 0, updated: 0, failed: 0, addedNames: [] };
  // Keyed by game AND provider: with sync_all_providers on a game has one tile per
  // service, all sharing the game_id, so a set of game ids can't tell them apart
  // and every tile past the first would read as "not desired" and be deleted.
  const desiredKeys = new Set(desired.map(trackingKey));

  // First make what we believe agree with what Steam has. The loop below works
  // from `tracked`, so a tile created but never recorded is invisible to it and
  // gets duplicated, and nothing removes either copy. shortcuts.vdf is the only
  // place that knows, and every tile of ours carries --game-id. strays.abandoned
  // is set when the wait is over so it stops touching `tracked` beside the loop.
  const strays = { abandoned: false };
  const strayTimer = setTimeout(() => { strays.abandoned = true; }, RPC_TIMEOUT);
  await withTimeout(adoptStrayShortcuts(tracked, callbacks, strays),
                    undefined, RPC_TIMEOUT);
  strays.abandoned = true;
  clearTimeout(strayTimer);

  // Tiles are filed into their provider's collection in batches as the sync runs,
  // not all at once at the end (an interrupted sync used to file nothing). Saving
  // a collection makes Steam re-read its library, so batching spreads that out.
  // Batched rather than per-game because Save() rewrites the whole collection.
  const COLLECTION_BATCH = 12;
  let pending: CollectionMember[] = [];
  // Kept as well as the batches. Filing happens a batch at a time so tiles appear
  // while the sync runs, but deciding what does NOT belong needs the whole
  // answer: a slice can't tell "filed elsewhere" from "not reached yet".
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

  // Count the REAL work up front (adds, updates, removals), not every game walked
  // past. Counting all of them made the total disagree with the button ("Add 124
  // playable games"), which reads as a bug though both numbers were right.
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
  // Names vary a lot in length, and a message that wraps to two lines and back
  // makes the whole panel to jump as it ticks through hundreds of games. Truncating
  // keeps every progress line the same height.
  const MAX_NAME_LEN = 22;
  const shorten = (name: string) => (name.length > MAX_NAME_LEN ? `${name.slice(0, MAX_NAME_LEN - 1)}…` : name);
  const step = (label: string, name: string) => {
    done++;
    callbacks.onProgress?.(`${label} ${shorten(name)} (${done}/${total})`);
  };
  // Say what is being looked at, WITHOUT a fraction. The denominator counts only
  // games that need work, so it can't move for a game that needs none, but on a
  // library of nine hundred the scan alone is half a minute and a frozen line
  // looks stuck. So the name moves and the fraction does not.
  const scanning = (name: string) => {
    callbacks.onProgress?.(`Checking ${shorten(name)} (${done}/${total})`);
  };

  // Web pages go first, and a cancel never reaches them. Cancelling "add all my
  // games" should drop the bulk still to come, not the handful of pages, which are
  // every one deliberate. Ordering alone is not enough (a page added after the
  // sync started would still sit behind the cancel), so they are exempt too.
  const ordered = [
    ...desired.filter((g) => WEBSITE_PROVIDERS.has(g.provider)),
    ...desired.filter((g) => !WEBSITE_PROVIDERS.has(g.provider)),
  ];

  // Where the installed layouts live, asked once for the whole run. Needed to
  // SELECT a layout for a tile the sync is not otherwise touching. Best effort:
  // without it those tiles keep the behaviour they had.
  const selectionBase = await withTimeout(
    layoutSelectionBase(), undefined, RPC_TIMEOUT)
    .then((r) => (r?.ok && r.base && r.native
      ? { base: r.base, native: r.native, repair: r.repair === true,
          repairRev: r.repair_rev }
      : undefined))
    .catch(() => undefined);

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
      // The whole turn under one deadline. An abandoned turn must STOP, not just
      // be stopped waiting for: withTimeout gives up on the promise but can't
      // cancel it, so an orphaned turn keeps issuing backend calls and spends the
      // same RPC slots as the live game, slowing the next one until it times out
      // too. Self-reinforcing.
      const turn = { abandoned: false };
      const turnTimer = setTimeout(() => { turn.abandoned = true; },
                                   PER_GAME_TIMEOUT);
      const finished = await withTimeout((async () => {
      if (!existing) {
        step("Adding", game.name);
        // 0 on timeout, which the check below already treats as a refusal.
        const appId = await withTimeout(SteamClient.Apps.AddShortcut(
          title, game.exe, START_DIR, game.options,
        ), 0);
        if (!appId) throw new Error("Steam refused the shortcut");
        if (turn.abandoned) return false;

        // AddShortcut only reliably applies the exe; name and launchOptions are
        // silently dropped, which made tiles called "python3" that exited at once.
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

        // Record the fingerprint only once the VISIBLE slot went on, else a game
        // whose capsule failed but whose hero and logo went on counts as done: a
        // blank tile with a full detail page, never retried.
        if (turn.abandoned) return false;
        const appliedNew = await applyArtwork(appId, game, turn);
        if (appliedNew.applied === 0) artUnavailable.add(game.game_id);
        // Same guard as the update path: an unsettled logo leaves the fingerprint
        // empty so the next sync retries -- but bounded, see logoRecorded.
        const recordedNew = appliedNew.visible && logoRecorded(appliedNew, game) ? art : "";
        await withTimeout(
          callbacks.record(trackingKey(game), appId, title, game.options,
                           recordedNew, game.provider,
                           game.url ?? "", String(game.steam_app_id ?? "")),
          undefined, RPC_TIMEOUT);
        file({ appId, provider: game.provider });
        result.added++;
        result.addedNames.push(game.name);
        // File this batch now, so the tiles show in their collection while the
        // rest of the sync runs.
        if (pending.length >= COLLECTION_BATCH) await flushCollections();
      } else {
        const launchChanged = existing.launch !== game.options || existing.name !== title;
        const artChanged = (existing.art ?? "") !== art;

        // Install the layout only when this tile is actually touched. It used to
        // run for EVERY game with a layout (891 backend calls here, all to find
        // nothing needed writing). A provider's layout does change sometimes,
        // which is what bumping ART_REVISION is for. `repair` forces the INSTALL
        // too: gating it is wrong when the NAMING RULE changes, since the files
        // still sit under the old names and Steam clears the tile to default when
        // handed a path with nothing at it. So a repair pass rewrites them.
        if (turn.abandoned) return false;
        if (game.controller_layout
            && (launchChanged || artChanged || selectionBase?.repair)) {
          try {
            const layoutResult = await withTimeout(
              installControllerLayout(title, game.controller_layout), undefined, RPC_TIMEOUT);
            const liveUrl = layoutResult?.autosave_url ?? game.controller_layout;
            await applyControllerConfig(existing.app_id, liveUrl, false,
                                        selectionBase?.repair === true);
            layoutSelected.add(existing.app_id);
          } catch { /* best effort */ }
        } else if (game.controller_layout && selectionBase
                   && !layoutSelected.has(existing.app_id)) {
          // SELECTING is not INSTALLING. Installing writes files and only for a
          // touched tile; selecting is a pure Steam-client call that must run for
          // every tile, because Steam does not pick our config up on its own. It
          // used to run only when a tile was CREATED, so a settled library fell
          // back to Steam's default (844 tiles here). The path is composed from one
          // base fetched once per sync, avoiding the 891-calls-a-sync above.
          try {
            // Lowercased, that is the name STEAM uses. See _layout_key in main.py:
            // it reports under ".../assetto corsa rally (boosteroid)/…" while we
            // wrote ".../Assetto Corsa Rally (Boosteroid)/…", and on a
            // case-sensitive filesystem those differ.
            await applyControllerConfig(
              existing.app_id,
              `autosave://${selectionBase.base}/${layoutKey(title)}`
                + `/controller_${selectionBase.native}.vdf`,
              false, selectionBase.repair);
            layoutSelected.add(existing.app_id);
          } catch { /* best effort */ }
        }

        // Artwork is compared apart from the launch command: the gateway resolves
        // better art over time, so a tile whose command never changes still picks
        // it up rather than staying blank.

        if (launchChanged) {
          step("Updating", game.name);
          SteamClient.Apps.SetShortcutName(existing.app_id, title);
          SteamClient.Apps.SetShortcutExe(existing.app_id, game.exe);
          SteamClient.Apps.SetShortcutStartDir(existing.app_id, START_DIR);
          SteamClient.Apps.SetShortcutLaunchOptions(existing.app_id, game.options);
        }
        // Do not re-attempt art already known unavailable this session
        const tryArt = artChanged && !artUnavailable.has(game.game_id);

        let appliedArt: ArtworkOutcome = { applied: 0, visible: false, logoSettled: true };
        if (tryArt) {
          if (!launchChanged) step("Artwork", game.name);
          if (turn.abandoned) return false;
          appliedArt = await applyArtwork(existing.app_id, game, turn);
          if (appliedArt.applied === 0) artUnavailable.add(game.game_id);
        }
        if (launchChanged || tryArt) {
          // Keep the old fingerprint until the visible slot goes on AND the logo
          // settled, so a later session tries again rather than marking a blank
          // tile (or one wearing a drawn fallback over a real logo it could not
          // fetch) as finished.
          const recordedArt = tryArt && (!appliedArt.visible || !logoRecorded(appliedArt, game))
            ? (existing.art ?? "") : art;
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
      clearTimeout(turnTimer);
      if (!finished) {
        // Named, because this is the only evidence of WHAT hangs. The same title
        // every time is a bad tile; a different one each run is something about
        // the client, not the game.
        console.warn("[shelfcloud] gave up on", game.name, "after",
                     PER_GAME_TIMEOUT, "ms — moving on");
        // Into the log file too, not just the console: the only trace that
        // outlives the sync. Bounded, so reporting a hang can't hang.
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
    // A pre-split entry is keyed by game id alone and matches no desired key, so
    // it would be removed here as unwanted, but the tile it names is one the sync
    // just adopted and updated. Skip it while any desired tile still claims that
    // game; the record is re-keyed on the next write.
    if (!key.includes(":") && desired.some((g) => String(g.game_id) === key)) continue;
    // Removals are cancellable too. The check used to be on the ADD loop alone, so
    // Cancel did nothing during "Remove all games" (an empty desired list means the
    // add loop has nothing to do and execution falls straight into this one).
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

  // Backfill the Steam appids once for every tile. record_shortcut carries this
  // too, but only for tiles a sync TOUCHES, and a settled library touches none, so
  // the player badge had nothing to ask Steam about. One call with the whole map
  // reaches them all without re-applying artwork.
  const steamIds: Record<string, string> = {};
  for (const game of ordered) {
    const id = game.steam_app_id;
    // Only games that ARE on Steam. About one in six is not, and those get no
    // entry rather than an empty one.
    if (id !== undefined && id !== null && String(id)) {
      steamIds[trackingKey(game)] = String(id);
    }
  }
  if (Object.keys(steamIds).length) {
    await withTimeout(setSteamIds(steamIds), undefined, RPC_TIMEOUT);
  }

  await phase("flush collections");
  await withTimeout(flushCollections(), undefined, RPC_TIMEOUT);

  // Take out what no longer belongs, but NOT after a cancel. Pruning answers from
  // `filed`, so it must see the whole sync: a slice can't tell "filed elsewhere"
  // from "not reached yet". A cancelled run is a slice, so pruning it would strip
  // every tile the loop never got to. The next complete run does it instead.
  if (!result.cancelled) {
    // Managed means "filed by this sync OR tracked before it". The second half
    // matters: a game dropped from the library is not filed anywhere now and would
    // otherwise sit in its old collection forever. Other apps are the user's own.
    const managed = new Set<number>(filed.map((m) => m.appId));
    for (const entry of Object.values(tracked)) managed.add(entry.app_id);
    await phase("prune collections");
  await withTimeout(pruneProviderCollections(filed, managed), undefined, RPC_TIMEOUT);
    // Last, and only on a complete run: drop a collection we made under a label we
    // no longer use ("custom (Shelfcloud)" from before website tiles were labelled
    // "Websites"). Its games were just filed under the current name, so it is empty.
    await phase("retire collections");
  await withTimeout(removeRetiredCollections(managed), undefined, RPC_TIMEOUT);

  // The one-off layout repair is done; record it so the next sync goes back to
  // leaving a user's own choice alone. Only after a run that was not cancelled,
  // or a repair stopped halfway would be marked complete.
  if (selectionBase?.repair && !result.cancelled) {
    await withTimeout(setSetting("layout_repair", selectionBase.repairRev),
                      undefined, RPC_TIMEOUT)
      .catch(() => undefined);
  }
  await phase("done");
  }

  return result;
}
