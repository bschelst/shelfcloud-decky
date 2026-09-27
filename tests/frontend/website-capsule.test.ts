// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which tiles get a capsule drawn for them, and what it is drawn from.
//
// pagecapsule.test.ts covers the composition; this covers the wiring: who the
// renderer is called for, and what picture it is handed. No canvas here, so the
// renderer is a stub.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { ART_REVISION, artworkFingerprint, syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({ layoutSelectionBase: vi.fn(async () => ({ ok: false })), logNote: vi.fn(async () => ({ ok: true })), fetchArtwork: vi.fn(), saveIcon: vi.fn() }));
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
    // the published page has the same problem: its capsule is a mark, not a name.
    await syncLibrary([page({ provider: "shelfcloud", name: "Shelfcloud Website" })], {}, callbacks());
    expect(renderPageCapsule).toHaveBeenCalledWith("Shelfcloud Website", expect.anything());
    expect(capsuleBytes).toBe("DRAWN");
  });

  it("draws the mark from the icon, never from the capsule", async () => {
    // draw from the icon (256px square mark), not the capsule (600x900), wich
    // would squash the cloud into a box.
    await syncLibrary([page()], {}, callbacks());
    expect(renderPageCapsule).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ data: "bytes:icon" }),
    );
  });
});

describe("reaching tiles that are already on a shelf", () => {
  it("changes a website tile's fingerprint", async () => {
    // same nameless capsule at the same URL, so the fingerprint would not move
    // without our own marker, already-synced website tiles keep the blank capsule.
    const before = `${ART_REVISION}|||||`;         // the shape before this change
    expect(artworkFingerprint(page())).not.toBe(before);
    expect(artworkFingerprint(page())).toContain("w1");
  });

  it("leaves every game's fingerprint exactly as it was", async () => {
    // why the marker is website-only, not an ART_REVISION bump: that would
    // re-download four slots for the whole library. Pinned byte for byte because
    // the marker is mixed into the first field, not added as its own.
    const game = page({ provider: "geforce_now", artwork: { capsule: "https://cdn.test/c" } });
    // against the CURRENT revision, not a literal digit: this test is about the
    // marker staying out of a game's fingerprint, not about the revision value.
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
    // a missing icon is a failed download; keep old tile over a markless one.
    const noIcon = page();
    delete (noIcon.artwork_proxy as Record<string, string>).icon;
    await syncLibrary([noIcon], {}, callbacks());
    expect(renderPageCapsule).not.toHaveBeenCalled();
    expect(capsuleBytes).toBe("bytes:capsule");
  });

  it("keeps the gateway capsule when drawing fails", async () => {
    // canvas refused; teh tile still gets a picture, just the old one.
    vi.mocked(renderPageCapsule).mockResolvedValue(undefined);
    await syncLibrary([page()], {}, callbacks());
    expect(capsuleBytes).toBe("bytes:capsule");
  });
});
