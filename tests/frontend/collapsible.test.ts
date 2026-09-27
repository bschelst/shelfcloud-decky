// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Only one panel section open at a time. On a 7-inch screen two open sections
// push the control below off the bottom while you reach for it. The component
// pulls in @decky/ui (needs a `window` we lack here), so the rule lives in a
// pure function, same split as the logo and badge layouts.

import { describe, expect, it } from "vitest";

import { nextOpenId } from "../../src/lib/accordion";

describe("choosing which section is open", () => {
  it("opens the one activated when nothing is open", () => {
    expect(nextOpenId(null, "Settings")).toBe("Settings");
  });

  it("closes the previous one when another is opened", () => {
    // expand Settings, then Links, and Settings folds away.
    expect(nextOpenId("Settings", "Links")).toBe("Links");
    expect(nextOpenId("Links", "About")).toBe("About");
  });

  it("closes the open one when it is activated again", () => {
    // not a no-op: without it the last opened section can never be put away.
    expect(nextOpenId("About", "About")).toBe(null);
  });

  it("never reports two open at once", () => {
    // walked over a real sequence: the answer is always one section or none.
    let open: string | null = null;
    for (const pressed of ["Settings", "Links", "Links", "About", "Settings"]) {
      open = nextOpenId(open, pressed);
      expect(open === null || typeof open === "string").toBe(true);
    }
    expect(open).toBe("Settings");
  });
});
