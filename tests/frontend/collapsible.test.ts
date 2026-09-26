// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Only one panel section open at a time.
//
// The Quick Access panel is read on a 7-inch screen with a thumbstick.
// Settings, Links and About are each long enough that two open together push
// whatever is below them off the bottom, so the control you were reaching for
// moves while you reach for it.
//
// The component cannot be rendered here -- importing it pulls in @decky/ui,
// which wants a `window` this suite does not have -- so the rule lives in a
// pure function and that is what these drive, the same split the logo and
// badge layouts use.

import { describe, expect, it } from "vitest";

import { nextOpenId } from "../../src/lib/accordion";

describe("choosing which section is open", () => {
  it("opens the one activated when nothing is open", () => {
    expect(nextOpenId(null, "Settings")).toBe("Settings");
  });

  it("closes the previous one when another is opened", () => {
    // The actual request: expand Settings, then Links, and Settings folds away.
    expect(nextOpenId("Settings", "Links")).toBe("Links");
    expect(nextOpenId("Links", "About")).toBe("About");
  });

  it("closes the open one when it is activated again", () => {
    // Not a no-op. Without this the last section opened can never be put
    // away, and the panel keeps its tallest shape for the rest of the session.
    expect(nextOpenId("About", "About")).toBe(null);
  });

  it("never reports two open at once", () => {
    // The property that matters, walked over a realistic sequence: whatever
    // the order, the answer is one section or none -- never a set.
    let open: string | null = null;
    for (const pressed of ["Settings", "Links", "Links", "About", "Settings"]) {
      open = nextOpenId(open, pressed);
      expect(open === null || typeof open === "string").toBe(true);
    }
    expect(open).toBe("Settings");
  });
});
