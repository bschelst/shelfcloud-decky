// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The directory name Steam uses for a shortcut's controller config.
//
// This must agree with _layout_key in main.py character for character: the
// backend WRITES the layout under that name and this composes the URL that
// SELECTS it. Any disagreement is a layout Steam silently never applies --
// no error, no log line, just the default template on every tile.
//
// The expectations below are not invented. Each is what Steam itself reported
// through GetConfigForAppAndController for that shortcut on hardware.

import { describe, expect, it } from "vitest";

import { layoutKey } from "../../src/lib/layoutKey";

describe("the name Steam gives a shortcut's config directory", () => {
  it("lower-cases it", () => {
    expect(layoutKey("Left 4 Dead 2 (Boosteroid)"))
      .toBe("left 4 dead 2 (boosteroid)");
  });

  it("drops full stops", () => {
    // Lower-casing alone left this one broken: Steam looks for "repo".
    expect(layoutKey("R.E.P.O. (Boosteroid)")).toBe("repo (boosteroid)");
    expect(layoutKey("Crash Bandicoot N. Sane Trilogy (GeForce NOW)"))
      .toBe("crash bandicoot n sane trilogy (geforce now)");
  });

  it("drops colons", () => {
    expect(layoutKey("BLiNX: The Time Sweeper (Xbox Cloud)"))
      .toBe("blinx the time sweeper (xbox cloud)");
    expect(layoutKey("World War Z: Aftermath (Xbox Cloud)"))
      .toBe("world war z aftermath (xbox cloud)");
  });

  it("drops pipes", () => {
    expect(layoutKey("Control Ultimate Edition - Xbox Series X|S (Xbox Cloud)"))
      .toBe("control ultimate edition - xbox series xs (xbox cloud)");
  });

  it("drops the rest of the Windows-illegal set", () => {
    expect(layoutKey('A\\B/C:D*E?F"G<H>I|J.K')).toBe("abcdefghijk");
  });

  it("keeps everything else, including spaces and brackets", () => {
    // The provider suffix in brackets is how a streamed copy is told apart
    // from an installed one, so it has to survive.
    expect(layoutKey("A Way Out (Boosteroid)")).toBe("a way out (boosteroid)");
    expect(layoutKey("Disney/Pixar WALL-E (GeForce NOW)"))
      .toBe("disneypixar wall-e (geforce now)");
  });

  it("leaves a name that needs nothing done to it alone", () => {
    expect(layoutKey("repo (boosteroid)")).toBe("repo (boosteroid)");
  });

  it("never produces a path separator", () => {
    // Which is also why the directory cannot nest: "/" is in the strip set, so
    // the result is one path segment by construction rather than by checking.
    expect(layoutKey("Disney/Pixar WALL-E")).not.toContain("/");
    expect(layoutKey("back\\slash")).not.toContain("\\");
  });
});
