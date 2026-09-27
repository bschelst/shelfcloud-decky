// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Turning "a tile per service" on has to bring the games in, not just reshape
// the ones already there. The option fans a game out across every service, but
// does nothing for a service whose games were never added -- so it must fetch
// too, else it reads as broken.

import { describe, expect, it, vi } from "vitest";

import { applyTilePerService } from "../../src/lib/settings";
import type { Status } from "../../src/lib/types";

const STATUS = { sync_minutes: 3 } as unknown as Status;

function deps(overrides: Partial<{ onEnabled: () => void }> = {}) {
  const order: string[] = [];
  return {
    order,
    setSetting: vi.fn(async (_k: string, _v: unknown) => {
      order.push("set");
      return STATUS;
    }),
    setStatus: vi.fn(),
    onEnabled: vi.fn(() => { order.push("add"); }),
    ...overrides,
  };
}

describe("switching it on", () => {
  it("stores the setting", async () => {
    const d = deps();
    await applyTilePerService(true, d);
    expect(d.setSetting).toHaveBeenCalledWith("sync_all_providers", true);
  });

  it("brings in the games the subscriptions cover", async () => {
    const d = deps();
    await applyTilePerService(true, d);
    expect(d.onEnabled).toHaveBeenCalledTimes(1);
  });

  it("stores the setting BEFORE fetching them", async () => {
    // the fetch ends in a sync; a sync reading the old value would fan nothing
    // out and the games arrive in the shape the option meant to change
    const d = deps();
    await applyTilePerService(true, d);
    expect(d.order).toEqual(["set", "add"]);
  });

  it("hands the fresh status back to the panel", async () => {
    const d = deps();
    await applyTilePerService(true, d);
    expect(d.setStatus).toHaveBeenCalledWith(STATUS);
  });
});

describe("switching it off", () => {
  it("stores the setting", async () => {
    const d = deps();
    await applyTilePerService(false, d);
    expect(d.setSetting).toHaveBeenCalledWith("sync_all_providers", false);
  });

  it("fetches nothing — switching off only takes tiles away", async () => {
    const d = deps();
    await applyTilePerService(false, d);
    expect(d.onEnabled).not.toHaveBeenCalled();
  });
});

describe("a panel that passes no callback", () => {
  it("still stores the setting rather than throwing", async () => {
    const d = deps();
    await expect(
      applyTilePerService(true, { setSetting: d.setSetting, setStatus: d.setStatus }),
    ).resolves.toBeUndefined();
    expect(d.setSetting).toHaveBeenCalled();
  });
});

describe("while a sync is already running", () => {
  it("still stores the setting", async () => {
    // the toggle used to be disabled during a sync, so a tap at the wrong
    // moment did nothing silently -- looked like a setting that wont turn on.
    const d = deps();
    await applyTilePerService(true, { ...d, busy: true });
    expect(d.setSetting).toHaveBeenCalledWith("sync_all_providers", true);
  });

  it("does not start a second bulk add on top of the run in flight", async () => {
    const d = deps();
    await applyTilePerService(true, { ...d, busy: true });
    expect(d.onEnabled).not.toHaveBeenCalled();
  });
});
