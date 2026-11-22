// A tile whose real Steam shortcut is not what we recorded gets rewritten. 130
// tiles on a steammachine still ran the older desktop app under their old name,
// the record said ours, so sync saw nothing to do and Play did nothing.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { artworkFingerprint, shortcutTitle, syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({
  layoutSelectionBase: vi.fn(async () => ({ ok: false })),
  logNote: vi.fn(async () => ({ ok: true })),
  fetchArtwork: vi.fn(async () => ({ ok: false })),
  saveIcon: vi.fn(),
  saveIconFromUrl: vi.fn(),
  tilesMissingIcons: vi.fn(async () => ({ ok: true, app_ids: [] })),
  tilesDrifted: vi.fn(),
}));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));

const { tilesDrifted } = await import("../../src/lib/api");

function game(id: number): DesiredGame {
  return {
    game_id: id, name: "Against the Storm", provider: "geforce_now",
    exe: "/usr/bin/python3",
    options: `/plugin/bin/shelfcloud-launch.py com.microsoft.Edge /p --game-id ${id} --provider geforce_now`,
    artwork: {},
  } as DesiredGame;
}

function settled(g: DesiredGame, appId: number) {
  return {
    [`${g.game_id}:${g.provider}`]: {
      app_id: appId, name: shortcutTitle(g), launch: g.options,
      art: artworkFingerprint(g), provider: g.provider,
    },
  };
}

const callbacks = () => ({ record: vi.fn(async () => {}), forget: vi.fn(async () => {}) });
let apps: Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  vi.clearAllMocks();
  apps = {
    AddShortcut: vi.fn(async () => 7777), SetShortcutName: vi.fn(), SetShortcutExe: vi.fn(),
    SetShortcutStartDir: vi.fn(), SetShortcutLaunchOptions: vi.fn(), RemoveShortcut: vi.fn(),
    SetShortcutIcon: vi.fn(), SetCustomArtworkForApp: vi.fn(async () => undefined),
    ClearCustomArtworkForApp: vi.fn(),
  };
  (globalThis as Record<string, unknown>).SteamClient = { Apps: apps };
});

describe("drift repair", () => {
  it("rewrites a settled tile whose real shortcut differs", async () => {
    const g = game(741);
    vi.mocked(tilesDrifted).mockResolvedValue({ ok: true, app_ids: [3049655096] });
    await syncLibrary([g], settled(g, 3049655096), callbacks());
    expect(apps.SetShortcutExe).toHaveBeenCalledWith(3049655096, "/usr/bin/python3");
    expect(apps.SetShortcutLaunchOptions).toHaveBeenCalledWith(3049655096, g.options);
    expect(apps.SetShortcutName).toHaveBeenCalledWith(3049655096, shortcutTitle(g));
  });

  it("leaves a settled tile that matches alone", async () => {
    const g = game(742);
    vi.mocked(tilesDrifted).mockResolvedValue({ ok: true, app_ids: [] });
    await syncLibrary([g], settled(g, 3049655097), callbacks());
    expect(apps.SetShortcutExe).not.toHaveBeenCalled();
  });

  it("rewrites once per session, not every sync", async () => {
    const g = game(743);
    vi.mocked(tilesDrifted).mockResolvedValue({ ok: true, app_ids: [3049655098] });
    await syncLibrary([g], settled(g, 3049655098), callbacks());
    apps.SetShortcutExe.mockClear();
    await syncLibrary([g], settled(g, 3049655098), callbacks());
    expect(apps.SetShortcutExe).not.toHaveBeenCalled();
  });

  it("a backend that can not answer only skips the repair", async () => {
    const g = game(744);
    vi.mocked(tilesDrifted).mockRejectedValue(new Error("did not answer"));
    await expect(syncLibrary([g], settled(g, 3049655099), callbacks())).resolves.toBeDefined();
    expect(apps.SetShortcutExe).not.toHaveBeenCalled();
  });
});
