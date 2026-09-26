// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Artwork ordering and concurrency.
//
// Each tile has four art slots, and every one is a separate download through
// the Python backend (Steam's client blocks the direct fetches: SteamGridDB
// sends no CORS header and the gateway is plain HTTP on an HTTPS page). They
// used to be fetched strictly one after another, so every game cost four round
// trips end to end and artwork trickled in for minutes during a large
// "add all my games".
//
// Two properties are asserted here because both are invisible from the code
// reading correct — a sequential version and a parallel one look almost
// identical on the page:
//
//   1. the capsule, the only slot visible in the library grid, is applied
//      before the three that back the detail page
//   2. the downloads actually overlap
//
// Applying stays sequential: SetCustomArtworkForApp writes Steam's own library
// state, and the time is in the downloads anyway.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({ logNote: vi.fn(async () => ({ ok: true })), fetchArtwork: vi.fn(), saveIcon: vi.fn(), saveIconFromUrl: vi.fn() }));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));

const { fetchArtwork, saveIcon, saveIconFromUrl } = await import("../../src/lib/api");

/** Steam's asset-type enum, as declared in sync.ts. */
const CAPSULE = 0, HERO = 1, LOGO = 2, HEADER = 3, ICON = 4;

const GAME: DesiredGame = {
  game_id: 1,
  name: "Fortnite",
  provider: "geforce_now",
  exe: "/usr/bin/python3",
  options: "/plugin/bin/shelfcloud-launch.py com.google.Chrome /profile https://x/?a=1",
  artwork: {
    capsule: "https://cdn.test/capsule.jpg",
    header: "https://cdn.test/header.jpg",
    hero: "https://cdn.test/hero.jpg",
    logo: "https://cdn.test/logo.png",
  },
};

/** Slots applied, in the order Steam was told about them. */
let appliedOrder: number[] = [];
/** Slots Steam was told to forget. */
let clearedSlots: number[] = [];
/** Peak number of downloads in flight at once. */
let peakInFlight = 0;

function installSteamClient() {
  appliedOrder = [];
  clearedSlots = [];
  (globalThis as Record<string, unknown>).SteamClient = {
    Apps: {
      AddShortcut: vi.fn(async () => 4242),
      SetShortcutName: vi.fn(),
      SetShortcutExe: vi.fn(),
      SetShortcutStartDir: vi.fn(),
      SetShortcutLaunchOptions: vi.fn(),
      RemoveShortcut: vi.fn(),
      SetShortcutIcon: vi.fn(),
      SetCustomArtworkForApp: vi.fn(async (_app: number, _d: string, _k: string, type: number) => {
        appliedOrder.push(type);
      }),
      ClearCustomArtworkForApp: vi.fn(async (_app: number, type: number) => {
        clearedSlots.push(type);
      }),
    },
  };
}

/** A backend that takes real time, so overlap is observable. */
function slowArtwork(delayMs = 5) {
  let inFlight = 0;
  peakInFlight = 0;
  vi.mocked(fetchArtwork).mockImplementation(async () => {
    inFlight += 1;
    peakInFlight = Math.max(peakInFlight, inFlight);
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    inFlight -= 1;
    return { ok: true, base64: "aGk=", kind: "jpg" };
  });
}

const callbacks = () => ({ record: vi.fn(async () => {}), forget: vi.fn(async () => {}) });

beforeEach(() => {
  vi.clearAllMocks();
  installSteamClient();
  slowArtwork();
  vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/1.png" });
  vi.mocked(saveIconFromUrl).mockResolvedValue({ ok: true, path: "/icons/1.png" });
});

describe("ordering", () => {
  it("applies the capsule before the slots behind it", async () => {
    await syncLibrary([GAME], {}, callbacks());
    // The tile in the grid shows the capsule; it must not wait on the rest.
    expect(appliedOrder[0]).toBe(CAPSULE);
    expect(appliedOrder).toHaveLength(4);
  });

  it("applies every slot exactly once", async () => {
    await syncLibrary([GAME], {}, callbacks());
    expect([...appliedOrder].sort()).toEqual([CAPSULE, HERO, LOGO, HEADER].sort());
  });

  it("writes to Steam one slot at a time", async () => {
    // Concurrent writes to one appId are not documented as safe, and there is
    // nothing to win: the downloads are the slow part.
    let writesInFlight = 0;
    let peakWrites = 0;
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;
    apps.SetCustomArtworkForApp = vi.fn(async (_a: number, _d: string, _k: string, type: number) => {
      writesInFlight += 1;
      peakWrites = Math.max(peakWrites, writesInFlight);
      await new Promise((resolve) => setTimeout(resolve, 2));
      writesInFlight -= 1;
      appliedOrder.push(type);
    });

    await syncLibrary([GAME], {}, callbacks());
    expect(peakWrites).toBe(1);
  });
});

describe("concurrency", () => {
  it("overlaps the downloads instead of queuing them", async () => {
    await syncLibrary([GAME], {}, callbacks());
    // Sequential would peak at 1. All four are independent, so all four run.
    expect(peakInFlight).toBeGreaterThan(1);
    expect(peakInFlight).toBe(4);
  });

  it("is meaningfully faster than one round trip per slot", async () => {
    slowArtwork(25);
    const started = Date.now();
    await syncLibrary([GAME], {}, callbacks());
    const elapsed = Date.now() - started;
    // Sequential would be ~100ms for four 25ms fetches. Overlapped is ~25-50.
    expect(elapsed).toBeLessThan(90);
  });
});

describe("resilience", () => {
  it("one failing slot does not cost the others", async () => {
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;
    apps.SetCustomArtworkForApp = vi.fn(async (_a: number, _d: string, _k: string, type: number) => {
      if (type === HERO) throw new Error("Steam said no");
      appliedOrder.push(type);
    });

    const result = await syncLibrary([GAME], {}, callbacks());
    expect(appliedOrder).toContain(CAPSULE);
    expect(appliedOrder).toHaveLength(3);
    expect(result.added).toBe(1);      // the game still synced
    expect(result.failed).toBe(0);
  });

  it("falls back to the gateway when the direct URL yields nothing", async () => {
    const seen: string[] = [];
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) => {
      seen.push(url);
      if (url.startsWith("https://cdn.test/")) return { ok: false };
      return { ok: true, base64: "aGk=", kind: "png" };
    });

    await syncLibrary([{
      ...GAME,
      artwork_proxy: {
        capsule: "http://gateway/art/1/capsule",
        header: "http://gateway/art/1/header",
        hero: "http://gateway/art/1/hero",
        logo: "http://gateway/art/1/logo",
      },
    }], {}, callbacks());

    expect(seen.filter((u) => u.startsWith("http://gateway/"))).toHaveLength(4);
    expect(appliedOrder).toHaveLength(4);
    expect(appliedOrder[0]).toBe(CAPSULE);
  });

  it("uses the gateway for a slot that has no direct URL at all", async () => {
    // The website-tile case, and the one the test above does NOT cover: that
    // one gives every slot a direct URL and makes the fetch fail. Here the
    // direct URL is absent, which is what the gateway serves art off its own
    // disk for — a catalogue row with a null capsule whose
    // /api/artwork/<id>/capsule still answers with a picture.
    //
    // fetchSlot used to return early on a missing direct URL and never look at
    // the proxy, so exactly the tiles that only have a fallback got nothing.
    const seen: string[] = [];
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) => {
      seen.push(url);
      return { ok: true, base64: "aGk=", kind: "png" };
    });

    await syncLibrary([{
      ...GAME,
      artwork: { capsule: null, header: null, hero: null, logo: null },
      artwork_proxy: {
        capsule: "http://gateway/art/1/capsule",
        header: "http://gateway/art/1/header",
        hero: "http://gateway/art/1/hero",
        logo: "http://gateway/art/1/logo",
      },
    }], {}, callbacks());

    expect(seen.every((u) => u.startsWith("http://gateway/"))).toBe(true);
    expect(appliedOrder).toHaveLength(4);
    expect(appliedOrder[0]).toBe(CAPSULE);
  });

  it("a slot with neither a URL nor a proxy is simply left empty", async () => {
    // Website tiles have no logo on purpose: an empty logo slot is how Steam
    // knows to draw the tile's name instead of a picture. That must stay a
    // quiet no-op rather than becoming a failed download.
    vi.mocked(fetchArtwork).mockImplementation(async () => ({
      ok: true, base64: "aGk=", kind: "png",
    }));

    const result = await syncLibrary([{
      ...GAME,
      artwork: { ...GAME.artwork, logo: null },
      artwork_proxy: undefined,
    }], {}, callbacks());

    expect(appliedOrder).not.toContain(LOGO);
    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });

  it("stops between tiles when asked, leaving the ones it made intact", async () => {
    // "Add all my games" walks a whole library and there was no way out of it.
    // Checked BETWEEN tiles on purpose: a tile is a shortcut plus artwork plus
    // a collection entry, and stopping mid-tile would leave a nameless entry
    // with no art that nothing tracks.
    const games = [GAME, { ...GAME, game_id: 2 }, { ...GAME, game_id: 3 }];
    let seen = 0;
    const result = await syncLibrary(games, {}, {
      ...callbacks(),
      shouldStop: () => seen++ >= 2,     // allow two, stop before the third
    });

    expect(result.cancelled).toBe(true);
    expect(result.added).toBe(2);
    expect(result.failed).toBe(0);
  });

  it("a cancel never drops the mandatory page, however early it comes", async () => {
    // Cancelling "add all my games" means "stop the bulk", not "stop at
    // whatever the loop happened to reach". The published page is pushed to
    // every account and custom pages were typed in by hand, so they are done
    // first AND exempt — a cancel aimed at three hundred owned games must not
    // cost the handful that were deliberate.
    const games = [
      { ...GAME, game_id: 1, provider: "boosteroid" },
      { ...GAME, game_id: 2, provider: "geforce_now" },
      { ...GAME, game_id: 3, provider: "shelfcloud" },   // pushed, last in the list
      { ...GAME, game_id: 4, provider: "custom" },       // hand-added page
    ];

    const result = await syncLibrary(games, {}, {
      ...callbacks(),
      shouldStop: () => true,              // cancelled before anything starts
    });

    // Both pages went on; neither cloud game did.
    expect(result.added).toBe(2);
    expect(result.cancelled).toBe(true);
  });

  it("does the pages FIRST, so progress shows them before the bulk", async () => {
    const order: string[] = [];
    const games = [
      { ...GAME, game_id: 1, provider: "boosteroid" },
      { ...GAME, game_id: 2, provider: "shelfcloud" },
      { ...GAME, game_id: 3, provider: "geforce_now" },
    ];
    await syncLibrary(games, {}, {
      ...callbacks(),
      record: async (key: string) => { order.push(key); },
    });
    expect(order[0]).toBe("2:shelfcloud");
  });

  it("stops DELETING when asked, which is the case cancel did nothing for", async () => {
    // Reported as: press Remove all games, press Cancel, the button changes to
    // "Stopping…" and every remaining tile is deleted anyway.
    //
    // The stop was checked in the ADD loop only. "Remove all games" passes an
    // empty desired list, so the add loop has nothing to do and execution
    // falls straight into the removal loop, which never asked.
    const tracked = Object.fromEntries(
      [1, 2, 3, 4, 5].map((id) => [String(id), {
        app_id: 1000 + id, name: `Game ${id}`, launch: "", art: "",
      }]),
    );
    let seen = 0;
    const result = await syncLibrary([], tracked, {
      ...callbacks(),
      shouldStop: () => seen++ >= 2,      // allow two removals, then stop
    });

    expect(result.cancelled).toBe(true);
    expect(result.removed).toBe(2);
    expect(result.removed).toBeLessThan(Object.keys(tracked).length);
  });

  it("removes everything when nothing asks it to stop", async () => {
    const tracked = Object.fromEntries(
      [1, 2, 3].map((id) => [String(id), {
        app_id: 2000 + id, name: `Game ${id}`, launch: "", art: "",
      }]),
    );
    const result = await syncLibrary([], tracked, {
      ...callbacks(),
      shouldStop: () => false,
    });
    expect(result.cancelled).toBeFalsy();
    expect(result.removed).toBe(3);
  });

  it("runs to the end when nothing asks it to stop", async () => {
    const games = [GAME, { ...GAME, game_id: 2 }];
    const result = await syncLibrary(games, {}, {
      ...callbacks(),
      shouldStop: () => false,
    });
    expect(result.cancelled).toBeFalsy();
    expect(result.added).toBe(2);
  });

  it("a game with no artwork still syncs", async () => {
    const result = await syncLibrary([{ ...GAME, artwork: undefined }], {}, callbacks());
    expect(appliedOrder).toHaveLength(0);
    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });
});

describe("the icon", () => {
  // An icon is NOT a library asset. Steam stores it in shortcuts.vdf as a file
  // path and reads that file whenever it draws the icon, so it goes through
  // SetShortcutIcon with a path -- not SetCustomArtworkForApp with bytes.
  //
  // The first attempt used asset type 4 through SetCustomArtworkForApp, a
  // value inferred from a comment rather than verified, and artwork vanished
  // from tiles afterwards. These tests exist so that cannot come back.

  it("never goes through the custom-artwork call", async () => {
    await syncLibrary([GAME], {}, callbacks());
    expect(appliedOrder).not.toContain(ICON);
    expect(appliedOrder).toHaveLength(4);
  });

  it("is written with the path the backend saved it to", async () => {
    vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/1.png" });
  vi.mocked(saveIconFromUrl).mockResolvedValue({ ok: true, path: "/icons/1.png" });
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;

    await syncLibrary([GAME], {}, callbacks());

    expect(apps.SetShortcutIcon).toHaveBeenCalledWith(4242, "/icons/1.png");
  });

  it("falls back to the logo, without a fifth download", async () => {
    const seen: string[] = [];
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) => {
      seen.push(url);
      return { ok: true, base64: "bG9nbw==", kind: "png" };
    });
    vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/1.png" });
  vi.mocked(saveIconFromUrl).mockResolvedValue({ ok: true, path: "/icons/1.png" });

    await syncLibrary([GAME], {}, callbacks());

    expect(seen).toHaveLength(4);                       // four slots, no more
    expect(vi.mocked(saveIconFromUrl).mock.calls[0][1]).toContain("logo");
  });

  it("a failed save never costs the artwork that worked", async () => {
    vi.mocked(saveIconFromUrl).mockRejectedValue(new Error("disk full"));
    const result = await syncLibrary([GAME], {}, callbacks());
    expect(appliedOrder).toHaveLength(4);
    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });

  it("is skipped when the backend refuses the image", async () => {
    vi.mocked(saveIconFromUrl).mockResolvedValue({ ok: false, error: "bad image data" });
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;
    await syncLibrary([GAME], {}, callbacks());
    expect(apps.SetShortcutIcon).not.toHaveBeenCalled();
  });

  it("is not attempted for a game with no artwork at all", async () => {
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;
    await syncLibrary([{ ...GAME, artwork: undefined }], {}, callbacks());
    expect(apps.SetShortcutIcon).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------- finishing, or not
//
// A tile is "done" once its artwork fingerprint is recorded, and a recorded
// fingerprint is never revisited. So what counts as done decides whether a
// blank tile ever gets a second chance.
//
// It used to be "any slot applied". With the capsule failing while hero and
// logo succeeded that is two slots, which read as success — and produced a
// tile blank in the grid whose detail page had full artwork, marked finished
// and never retried. "007 First Light" looked exactly like that on hardware.

describe("when artwork counts as finished", () => {
  /** Like callbacks(), but with `record` typed so its arguments can be read.
   *  The fingerprint is argument 5. */
  const recording = () => ({
    record: vi.fn(async (
      _key: string, _appId: number, _name: string, _options: string,
      _art: string, _provider: string,
    ) => {}),
    forget: vi.fn(async () => {}),
  });

  /** Fail exactly one slot, by URL; every other download succeeds. */
  function failSlot(url: string) {
    vi.mocked(fetchArtwork).mockImplementation(async (wanted: string) =>
      (wanted === url
        ? { ok: false, error: "boom" }
        : { ok: true, base64: "aGk=", kind: "jpg" }) as never);
  }

  it("does not record a fingerprint when the visible slot failed", async () => {
    failSlot(GAME.artwork!.capsule!);
    const cb = recording();

    await syncLibrary([GAME], {}, cb);

    // The other slots still went on. That is the partial success which used to
    // be mistaken for a whole one.
    expect(appliedOrder).not.toContain(CAPSULE);
    expect(appliedOrder.length).toBeGreaterThan(0);
    expect(cb.record.mock.calls[0][4]).toBe("");     // empty = retry next sync
  });

  it("records a fingerprint when the visible slot went on but another failed", async () => {
    failSlot(GAME.artwork!.hero!);
    const cb = recording();

    await syncLibrary([GAME], {}, cb);

    expect(appliedOrder).toContain(CAPSULE);
    expect(cb.record.mock.calls[0][4]).not.toBe("");  // grid is right; stop
  });

  it("treats a game with no capsule art as settled rather than retrying forever", async () => {
    const cb = recording();
    await syncLibrary(
      [{ ...GAME, artwork: { hero: GAME.artwork!.hero, logo: GAME.artwork!.logo } }],
      {}, cb);

    // Nothing to put in the grid, so nothing to retry. Recording it stops the
    // next sync re-downloading art for a game that will never have a capsule.
    expect(cb.record.mock.calls[0][4]).not.toBe("");
  });

  it("re-applies art to a tile whose fingerprint predates the current revision", async () => {
    // Its own game_id, not GAME's. artUnavailable is module-scoped and lives
    // for the whole file — an earlier test syncs GAME with no artwork at all,
    // which marks id 1 unavailable for every test after it, and the update
    // path skips artwork for anything in that set. Reusing the id here tested
    // that set, not the revision bump.
    const game: DesiredGame = { ...GAME, game_id: 77 };
    const cb = recording();
    const tracked = {
      "77": {
        app_id: 4242,
        name: "Fortnite (GeForce NOW)",
        launch: game.options,
        // Revision 4 — recorded before partial failures were caught, which is
        // exactly why the bump exists: the URLs still match, so nothing else
        // would ever look at this tile again.
        art: `4|${game.artwork!.capsule}|${game.artwork!.header}`
           + `|${game.artwork!.hero}|${game.artwork!.logo}`,
      },
    };

    await syncLibrary([game], tracked, cb);

    expect(appliedOrder).toContain(CAPSULE);
  });
});

describe("a slot the gateway no longer offers", () => {
  // Website tiles used to carry the Shelfcloud cloud as their logo, and Steam
  // draws the logo INSTEAD OF the title — so every page somebody added showed
  // the same mark where its name should be. The gateway stopped serving it,
  // but a slot that is merely skipped keeps whatever was written last time.
  const noLogo = {
    ...GAME,
    artwork: { capsule: GAME.artwork!.capsule, header: GAME.artwork!.header, hero: GAME.artwork!.hero },
  };

  it("is cleared from Steam rather than left as it was", async () => {
    await syncLibrary([noLogo], {}, callbacks());

    expect(clearedSlots).toEqual([LOGO]);
    // And the slots that ARE offered still go on.
    expect([...appliedOrder].sort()).toEqual([CAPSULE, HERO, HEADER].sort());
  });

  it("does not clear a slot whose download merely failed", async () => {
    // The opposite case, and the reason the check is on what the entry offers
    // rather than on whether an image came back: clearing here would wipe good
    // artwork every time the network hiccuped.
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) =>
      url.includes("hero") ? { ok: false } : { ok: true, base64: "aGk=", kind: "jpg" });

    await syncLibrary([GAME], {}, callbacks());

    expect(clearedSlots).toEqual([]);
    expect(appliedOrder).not.toContain(HERO);
  });
});

describe("the icon has its own slot", () => {
  // It used to be taken from the logo, which tied two unrelated things
  // together: the only way to give a tile an icon was to give it a logo, and
  // Steam draws a logo INSTEAD OF the title. Every website tile showed the
  // same mark where its name should be. The gateway now serves a square icon
  // and no logo for those, so the two can differ.
  const withIcon = {
    ...GAME,
    artwork: { ...GAME.artwork!, logo: null, icon: "https://cdn.test/icon.png" },
  };

  it("prefers the icon over the logo", async () => {
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) => ({
      ok: true, base64: url.includes("icon") ? "aWNvbg==" : "b3RoZXI=", kind: "png",
    }));

    await syncLibrary([withIcon], {}, callbacks());

    expect(vi.mocked(saveIconFromUrl).mock.calls[0][1]).toContain("icon");
  });

  it("never writes the icon as a library asset", async () => {
    // The whole reason this slot is kept out of BACKGROUND_SLOTS. Writing it
    // as asset type 4 is what damaged tiles under revision 2.
    await syncLibrary([withIcon], {}, callbacks());

    expect(appliedOrder).not.toContain(ICON);
    expect([...appliedOrder].sort()).toEqual([CAPSULE, HERO, HEADER].sort());
  });

  it("still leaves a tile with no logo showing its name", async () => {
    // The tile gets an icon and the logo slot stays empty — which is the
    // point: an empty logo is how Steam knows to draw the title.
    await syncLibrary([withIcon], {}, callbacks());

    expect(clearedSlots).toEqual([LOGO]);
    expect(vi.mocked(saveIconFromUrl)).toHaveBeenCalled();
  });

  it("falls back to the capsule when there is neither", async () => {
    const bare = { ...GAME, artwork: { capsule: GAME.artwork!.capsule } };
    vi.mocked(fetchArtwork).mockImplementation(async () => ({
      ok: true, base64: "Y2Fwc3VsZQ==", kind: "jpg",
    }));

    await syncLibrary([bare], {}, callbacks());

    expect(vi.mocked(saveIconFromUrl).mock.calls[0][1]).toContain("capsule");
  });
});

describe("the artwork fingerprint", () => {
  it("changes when only the icon changes", async () => {
    // Otherwise a tile that already exists keeps its stored fingerprint, is
    // never revisited, and never receives the icon at all.
    const { artworkFingerprint } = await import("../../src/lib/sync");
    const before = artworkFingerprint(GAME);
    const after = artworkFingerprint({
      ...GAME, artwork: { ...GAME.artwork!, icon: "https://cdn.test/icon.png" },
    });
    expect(after).not.toBe(before);
  });
});
