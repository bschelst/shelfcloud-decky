// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// The numbers in the finished-sync line have to agree with each other.
//
// What shipped: "385 games — 385 added (7 skipped)". Read it as a sentence and
// it is nonsense — if 385 were added and 7 skipped, the library holds 392, not
// 385. The cause was that `total` was taken AFTER the skipped entries had been
// dropped, so the two numbers counted different populations while sitting side
// by side in one line, inviting the reader to find the 7 inside the 385.
//
// These tests state the invariant directly: whatever else the line says, the
// totals it shows must add up.

import { describe, expect, it } from "vitest";

import { changeSummary, syncSummary } from "../../src/lib/summary";
import type { SyncResult } from "../../src/lib/types";

const result = (over: Partial<SyncResult> = {}): SyncResult => ({
  added: 0, removed: 0, updated: 0, failed: 0, addedNames: [], ...over,
});

describe("the finished-sync line", () => {
  it("reports a total that includes the skipped entries", () => {
    // The exact case from the Deck: 392 in the library, 7 unusable, 385 added.
    expect(syncSummary(392, result({ added: 385 }), 7))
      .toBe("392 games — 385 added (7 skipped)");
  });

  it("shows a total the parts actually sum to", () => {
    const added = 385;
    const skipped = 7;
    const line = syncSummary(added + skipped, result({ added }), skipped);
    const total = Number(line.match(/^(\d+) games/)![1]);
    expect(total).toBe(added + skipped);
  });

  it("never claims more was added than the library holds", () => {
    for (const [total, added, skipped] of [[392, 385, 7], [10, 10, 0], [5, 0, 5]]) {
      const line = syncSummary(total, result({ added }), skipped);
      const shown = Number(line.match(/^(\d+) games/)![1]);
      expect(shown).toBeGreaterThanOrEqual(added);
    }
  });

  it("omits the skipped note entirely when nothing was skipped", () => {
    expect(syncSummary(120, result({ added: 120 }), 0))
      .toBe("120 games — 120 added");
    expect(syncSummary(120, result({ added: 120 }))).not.toContain("skipped");
  });

  it("says so plainly when a sync changed nothing", () => {
    expect(syncSummary(386, result())).toBe("386 games — already up to date");
  });

  it("lists every kind of change, in a stable order", () => {
    expect(syncSummary(40, result({ added: 1, removed: 2, updated: 3, failed: 4 })))
      .toBe("40 games — 1 added, 2 removed, 3 updated, 4 failed");
  });

  it("leaves out the kinds that did not happen", () => {
    // A run that only removed should not read "0 added, 3 removed".
    expect(changeSummary(result({ removed: 3 }))).toBe("3 removed");
  });
});

describe("the toast", () => {
  it("carries the changes without the totals", () => {
    // It can appear over a game in progress, so it stays short.
    const line = changeSummary(result({ added: 385 }));
    expect(line).toBe("385 added");
    expect(line).not.toContain("games");
    expect(line).not.toContain("skipped");
  });

  it("is the same wording the full line uses", () => {
    const r = result({ added: 2, updated: 1 });
    expect(syncSummary(9, r, 0)).toContain(changeSummary(r));
  });
});
