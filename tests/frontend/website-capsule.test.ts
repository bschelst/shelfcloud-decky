// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which tiles get a capsule drawn for them, and what it is drawn from.
//
// pagecapsule.test.ts covers the composition. This covers the wiring, which is
// a separate thing and broke separately: a renderer that works perfectly is
// worth nothing if the sync never calls it, calls it for real games too, or
// hands it the wrong picture to draw the mark from.
//
// There is no canvas in this suite, so the renderer is replaced by a stub. The
// assertions are about who it is called for and what comes back out at Steam.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ART_REVISION, artworkFingerprint, syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({ fetchArtwork: vi.fn(), saveIcon: vi.fn() }));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));
vi.mock("../../src/lib/pagecapsule", () => ({
  renderPageCapsule: vi.fn(async () => ({ data: "DRAWN", kind: "png" })),
}));

const { fetchArtwork, saveIcon } = await import("../../src/lib/api");
const { renderPageCapsule } = await import("../../src/lib/pagecapsule");

const CAPSULE = 0;

/** Every slot answers with bytes that say which URL they came from, so what
 *  Steam is handed can be traced back to a source. */
function artworkByUrl() {
  vi.mocked(fetchArtwork).mockImplementation(async (url: string) => {
    const slot = String(url).split("/").pop() ?? "";
    return { ok: true, base64: `bytes:${slot}`, kind: "png" };
  });
}

function page(over: Partial<DesiredGame> = {}): DesiredGame {
  return {
    game_id: 7,
    name: "Schelstraete Bart",
    provider: "custom",
    exe: "/usr/bin/python3",
    options: "/plugin/bin/shelfcloud-launch.py com.google.Chrome /p https://x/",
    artwork: {},
    artwork_proxy: {
      capsule: "https://gw.test/api/artwork/7/capsule",
      header: "https://gw.test/api/artwork/7/header",
      hero: "https://gw.test/api/artwork/7/hero",
      icon: "https://gw.test/api/artwork/7/icon",
    },
    ...over,
  } as DesiredGame;
}

/** What Steam was handed for the tile in the grid. */
let capsuleBytes: string | undefined;

beforeEach(() => {
  vi.clearAllMocks();
  capsuleBytes = undefined;
  vi.mocked(renderPageCapsule).mockResolvedValue({ data: "DRAWN", kind: "png" });
  vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/7.png" });
  artworkByUrl();
  (globalThis as Record<string, unknown>).SteamClient = {
    Apps: {
      AddShortcut: vi.fn(async () => 4242),
      SetShortcutName: vi.fn(),
      SetShortcutExe: vi.fn(),
      SetShortcutStartDir: vi.fn(),
      SetShortcutLaunchOptions: vi.fn(),
      RemoveShortcut: vi.fn(),
      SetShortcutIcon: vi.fn(),
      SetCustomArtworkForApp: vi.fn(async (_a: number, data: string, _k: string, type: number) => {
        if (type === CAPSULE) capsuleBytes = data;
      }),
      ClearCustomArtworkForApp: vi.fn(),
    },
  };
});

const callbacks = () => ({ record: vi.fn(async () => {}), forget: vi.fn(async () => {}) });

describe("a website tile gets its name on the tile", () => {
  it("draws the capsule for a page you added", async () => {
    await syncLibrary([page()], {}, callbacks());
    expect(renderPageCapsule).toHaveBeenCalledWith("Schelstraete Bart", expect.anything());
    expect(capsuleBytes).toBe("DRAWN");
  });

  it("draws it for the published page too", async () => {
    // The Shelfcloud page has the same problem as anybody else's: its capsule
    // is the bare mark, and a mark is not a name.
    await syncLibrary([page({ provider: "shelfcloud", name: "Shelfcloud Website" })], {}, callbacks());
    expect(renderPageCapsule).toHaveBeenCalledWith("Shelfcloud Website", expect.anything());
    expect(capsuleBytes).toBe("DRAWN");
  });

  it("draws the mark from the icon, never from the capsule", async () => {
    // The icon is the 256px square mark. The capsule is 600x900 with the cloud
    // already centred in it, so drawing THAT into the square would give a
    // squashed cloud in a box.
    await syncLibrary([page()], {}, callbacks());
    expect(renderPageCapsule).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ data: "bytes:icon" }),
    );
  });
});

describe("reaching tiles that are already on a shelf", () => {
  it("changes a website tile's fingerprint", async () => {
    // The gateway serves the same nameless capsule at the same URL, so nothing
    // the fingerprint is built from moved. Without a marker of our own, every
    // website tile already synced would keep the blank capsule forever.
    const before = `${ART_REVISION}|||||`;         // the shape before this change
    expect(artworkFingerprint(page())).not.toBe(before);
    expect(artworkFingerprint(page())).toContain("w1");
  });

  it("leaves every game's fingerprint exactly as it was", async () => {
    // The reason the marker is website-only rather than a bump of ART_REVISION.
    // Disturbing this re-downloads four slots for the whole library — hundreds
    // of games — to repaint a handful of website tiles.
    //
    // Pinned byte for byte rather than by prefix. The marker has to be mixed
    // INTO the first field: carrying it as a field of its own would leave a
    // game's fingerprint still starting "7|" while shifting every value one
    // place along, so a prefix check would pass while the whole library
    // re-synced anyway.
    const game = page({ provider: "geforce_now", artwork: { capsule: "https://cdn.test/c" } });
    // Against the CURRENT revision, not a literal. The point of this test is
    // that the website marker stays out of a game's fingerprint and does not
    // shift the fields along -- neither of which has anything to do with what
    // ART_REVISION happens to be, and pinning the digit made a deliberate
    // bump look like a regression in the website marker.
    expect(artworkFingerprint(game)).toBe(`${ART_REVISION}|https://cdn.test/c||||`);
    expect(artworkFingerprint(game)).not.toContain("w1");
  });

  it("tells the two apart even with identical artwork", async () => {
    const art = { capsule: "https://cdn.test/c" };
    expect(artworkFingerprint(page({ provider: "custom", artwork: art })))
      .not.toBe(artworkFingerprint(page({ provider: "geforce_now", artwork: art })));
  });
});

describe("what it leaves alone", () => {
  it("does not touch a real game's capsule", async () => {
    const game = page({
      provider: "geforce_now",
      name: "Fortnite",
      artwork: { capsule: "https://cdn.test/capsule" },
    });
    await syncLibrary([game], {}, callbacks());
    expect(renderPageCapsule).not.toHaveBeenCalled();
    expect(capsuleBytes).toBe("bytes:capsule");
  });

  it("keeps the gateway capsule when there is no icon to draw with", async () => {
    // Rather than drawing a capsule with no mark on it. A missing icon is a
    // failed download, and the tile it had before is better than half a tile.
    const noIcon = page();
    delete (noIcon.artwork_proxy as Record<string, string>).icon;
    await syncLibrary([noIcon], {}, callbacks());
    expect(renderPageCapsule).not.toHaveBeenCalled();
    expect(capsuleBytes).toBe("bytes:capsule");
  });

  it("keeps the gateway capsule when drawing fails", async () => {
    // Canvas refused, or Steam's UI has no 2d context. The tile still gets a
    // picture; it is just the old one.
    vi.mocked(renderPageCapsule).mockResolvedValue(undefined);
    await syncLibrary([page()], {}, callbacks());
    expect(capsuleBytes).toBe("bytes:capsule");
  });
});
