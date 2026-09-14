// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
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

vi.mock("../../src/lib/api", () => ({ fetchArtwork: vi.fn(), saveIcon: vi.fn() }));
vi.mock("../../src/lib/collection", () => ({ addToProviderCollections: vi.fn() }));

const { fetchArtwork, saveIcon } = await import("../../src/lib/api");

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
/** Peak number of downloads in flight at once. */
let peakInFlight = 0;

function installSteamClient() {
  appliedOrder = [];
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
    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;

    await syncLibrary([GAME], {}, callbacks());

    expect(apps.SetShortcutIcon).toHaveBeenCalledWith(4242, "/icons/1.png");
  });

  it("sends the logo, not a fifth download", async () => {
    const seen: string[] = [];
    vi.mocked(fetchArtwork).mockImplementation(async (url: string) => {
      seen.push(url);
      return { ok: true, base64: "bG9nbw==", kind: "png" };
    });
    vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/1.png" });

    await syncLibrary([GAME], {}, callbacks());

    expect(seen).toHaveLength(4);                       // four slots, no more
    expect(vi.mocked(saveIcon).mock.calls[0][1]).toBe("bG9nbw==");
  });

  it("a failed save never costs the artwork that worked", async () => {
    vi.mocked(saveIcon).mockRejectedValue(new Error("disk full"));
    const result = await syncLibrary([GAME], {}, callbacks());
    expect(appliedOrder).toHaveLength(4);
    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });

  it("is skipped when the backend refuses the image", async () => {
    vi.mocked(saveIcon).mockResolvedValue({ ok: false, error: "bad image data" });
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
