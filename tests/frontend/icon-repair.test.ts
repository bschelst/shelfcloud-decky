// A tile whose icon FILE is gone gets its artwork, icon included, put back on
// the next sync. A Decky update ran the old version's _uninstall, which deleted
// the icon of every tile while the tiles stayed: a grey circle on 377 of them,
// and nothing put it back because the gateway's art had not changed.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { artworkFingerprint, syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({
  layoutSelectionBase: vi.fn(async () => ({ ok: false })),
  logNote: vi.fn(async () => ({ ok: true })),
  fetchArtwork: vi.fn(),
  saveIcon: vi.fn(),
  saveIconFromUrl: vi.fn(),
  tilesMissingIcons: vi.fn(),
}));
vi.mock("../../src/lib/squareicon", () => ({
  renderSquareIcon: vi.fn(async () => ({ data: "SQUARE", kind: "png" })),
}));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));

const { fetchArtwork, saveIcon, saveIconFromUrl, tilesMissingIcons } = await import("../../src/lib/api");
const { renderSquareIcon } = await import("../../src/lib/squareicon");

function game(id: number): DesiredGame {
  return {
    game_id: id,
    name: "Fortnite",
    provider: "geforce_now",
    exe: "/usr/bin/python3",
    options: `/plugin/bin/shelfcloud-launch.py com.google.Chrome /p https://x/${id}`,
    artwork: {
      capsule: `https://cdn.test/${id}/capsule`,
      icon: `https://cdn.test/${id}/icon`,
    },
  } as DesiredGame;
}

/** Tracked exactly as the sync left it last time: nothing changed since. */
function settled(g: DesiredGame, appId: number) {
  return {
    [`${g.game_id}:${g.provider}`]: {
      app_id: appId, name: "Fortnite (GeForce NOW)", launch: g.options,
      art: artworkFingerprint(g), provider: g.provider,
    },
  };
}

const callbacks = () => ({ record: vi.fn(async () => {}), forget: vi.fn(async () => {}) });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchArtwork).mockImplementation(async (url: string) =>
    ({ ok: true, base64: `bytes:${url}`, kind: "png" }));
  vi.mocked(saveIconFromUrl).mockResolvedValue({ ok: true, path: "/grid/x_icon.png" });
  vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/grid/x_icon.png" });
  vi.mocked(renderSquareIcon).mockResolvedValue({ data: "SQUARE", kind: "png" });
  (globalThis as Record<string, unknown>).SteamClient = {
    Apps: {
      AddShortcut: vi.fn(async () => 7777), SetShortcutName: vi.fn(), SetShortcutExe: vi.fn(),
      SetShortcutStartDir: vi.fn(), SetShortcutLaunchOptions: vi.fn(), RemoveShortcut: vi.fn(),
      SetShortcutIcon: vi.fn(), SetCustomArtworkForApp: vi.fn(async () => undefined),
      ClearCustomArtworkForApp: vi.fn(),
    },
  };
});

describe("icon repair", () => {
  it("puts the icon back on a settled tile that lost it", async () => {
    const g = game(501);
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [4053800932] });
    await syncLibrary([g], settled(g, 4053800932), callbacks());
    expect(saveIconFromUrl).toHaveBeenCalledWith(501, "https://cdn.test/501/icon", 4053800932);
  });

  it("leaves a settled tile alone when its icon is there", async () => {
    const g = game(502);
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [] });
    await syncLibrary([g], settled(g, 4053800933), callbacks());
    expect(saveIconFromUrl).not.toHaveBeenCalled();
    expect(fetchArtwork).not.toHaveBeenCalled();
  });

  it("tries a tile once per session, not every sync", async () => {
    // an icon that can not be saved must not redo all art every 5 minutes
    const g = game(503);
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [4053800934] });
    await syncLibrary([g], settled(g, 4053800934), callbacks());
    vi.mocked(saveIconFromUrl).mockClear();
    await syncLibrary([g], settled(g, 4053800934), callbacks());
    expect(saveIconFromUrl).not.toHaveBeenCalled();
  });

  it("a backend that can not answer only skips the repair", async () => {
    const g = game(504);
    vi.mocked(tilesMissingIcons).mockRejectedValue(new Error("did not answer"));
    await expect(syncLibrary([g], settled(g, 4053800935), callbacks())).resolves.toBeDefined();
    expect(saveIconFromUrl).not.toHaveBeenCalled();
  });
});

describe("the icon of a game the gateway has no icon for", () => {
  // Fortnite on GeForce NOW: no icon, so the LOGO became the icon, white text
  // on nothing, drawn small on black. A square out of the capsule instead.
  const noIcon = (id: number): DesiredGame => {
    const g = game(id);
    return { ...g, artwork: { capsule: `https://cdn.test/${id}/capsule`, logo: `https://cdn.test/${id}/logo` } };
  };

  it("is a square cut from the capsule, not the logo", async () => {
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [] });
    await syncLibrary([noIcon(601)], {}, callbacks());
    expect(renderSquareIcon).toHaveBeenCalledWith(
      expect.objectContaining({ data: "bytes:https://cdn.test/601/capsule" }));
    expect(saveIcon).toHaveBeenCalledWith(601, "SQUARE", "png", expect.anything());
    expect(saveIconFromUrl).not.toHaveBeenCalledWith(601, "https://cdn.test/601/logo", expect.anything());
  });

  it("falls back on the logo when the square can not be drawn", async () => {
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [] });
    vi.mocked(renderSquareIcon).mockResolvedValue(undefined);
    await syncLibrary([noIcon(602)], {}, callbacks());
    expect(saveIconFromUrl).toHaveBeenCalledWith(602, "https://cdn.test/602/logo", expect.anything());
  });

  it("a real icon still wins", async () => {
    vi.mocked(tilesMissingIcons).mockResolvedValue({ ok: true, app_ids: [] });
    await syncLibrary([game(603)], {}, callbacks());
    expect(saveIconFromUrl).toHaveBeenCalledWith(603, "https://cdn.test/603/icon", expect.anything());
    expect(renderSquareIcon).not.toHaveBeenCalled();
  });
});
