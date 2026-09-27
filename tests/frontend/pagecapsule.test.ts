// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Putting a page's name on its capsule. Every website tile got the same cloud
// on navy with no words, so a shelf of them was a wall of identical squares.
// No canvas here, so the composition is a pure function taking a measuring
// callback, and that is where the failures hide: Steam clips an overflow
// silently.

import { describe, expect, it } from "vitest";

import { capsuleLayout, CAPSULE_W, CAPSULE_H } from "../../src/lib/pagecapsule";

/** Stand-in for canvas text measurement: every glyph is 0.55em. The arithmetic
 *  around the measurement is what is under test, not a real font. */
const measure = (text: string, size: number) => text.length * size * 0.55;

const widthOf = (layout: { lines: string[]; size: number }) =>
  Math.max(0, ...layout.lines.map((line) => measure(line, layout.size)));

/** Names worth being rude with -- plausible page names plus the odd ones. */
const NAMES = [
  "Wiki",
  "Schelstraete Bart",
  "The Hitchhikers Guide To The Galaxy Wiki",
  "Supercalifragilisticexpialidocious",
  "Supercalifragilisticexpialidocious".repeat(4),
  "A".repeat(200),
  "word ".repeat(60),
  "🎮🎮🎮",
  "",
];

describe("fitting the name onto a capsule", () => {
  it("sets a short name large", () => {
    const layout = capsuleLayout("Wiki", measure);
    expect(layout.lines).toEqual(["Wiki"]);
    expect(layout.size).toBeGreaterThanOrEqual(56);
  });

  it("has more room to wrap than the logo does", () => {
    // the capsule is tall, so a long name gets three lines, not a tiny size.
    const layout = capsuleLayout("The Hitchhikers Guide To The Galaxy Wiki", measure);
    expect(layout.lines.length).toBeGreaterThan(1);
    expect(layout.lines.length).toBeLessThanOrEqual(3);
    expect(layout.lines.join(" ")).toBe("The Hitchhikers Guide To The Galaxy Wiki");
  });

  it("truncates a single unbreakable word instead of overflowing", () => {
    const layout = capsuleLayout("Supercalifragilisticexpialidocious".repeat(4), measure);
    expect(layout.lines).toHaveLength(1);
    expect(layout.lines[0].endsWith("…")).toBe(true);
  });

  it("an empty name draws no words rather than an empty box", () => {
    for (const name of ["", "   ", "\n\t"]) {
      expect(capsuleLayout(name, measure).lines).toEqual([]);
    }
  });

  it("collapses runs of whitespace", () => {
    expect(capsuleLayout("  My    Wiki  ", measure).lines.join(" ")).toBe("My Wiki");
  });
});

describe("staying inside the tile", () => {
  it("never lets the words run past the edges", () => {
    for (const name of NAMES) {
      const layout = capsuleLayout(name, measure);
      expect(widthOf(layout)).toBeLessThanOrEqual(layout.textWidth + 0.5);
      // set centred, so both halves have to clear the edge.
      expect(layout.centreX - layout.textWidth / 2).toBeGreaterThanOrEqual(0);
      expect(layout.centreX + layout.textWidth / 2).toBeLessThanOrEqual(CAPSULE_W);
    }
  });

  it("keeps the mark and the name on the canvas, whatever the name", () => {
    for (const name of NAMES) {
      const layout = capsuleLayout(name, measure);
      expect(layout.mark.x).toBeGreaterThanOrEqual(0);
      expect(layout.mark.y).toBeGreaterThanOrEqual(0);
      expect(layout.mark.x + layout.mark.size).toBeLessThanOrEqual(CAPSULE_W);
      expect(layout.textTop + layout.textHeight).toBeLessThanOrEqual(CAPSULE_H);
    }
  });

  it("never draws the name over the mark", () => {
    // one stacked group: words above the mark's bottom edge would overlap it.
    for (const name of NAMES) {
      const layout = capsuleLayout(name, measure);
      expect(layout.textTop).toBeGreaterThanOrEqual(layout.mark.y + layout.mark.size);
    }
  });
});

describe("balance", () => {
  it("centres the mark horizontally", () => {
    const layout = capsuleLayout("Anything", measure);
    expect(layout.mark.x + layout.mark.size / 2).toBeCloseTo(CAPSULE_W / 2, 5);
  });

  it("keeps the group centred as the name grows", () => {
    // mark and name move together; without it a three-line name hangs off the
    // bottom while a one-word page floats with the tile half empty.
    const short = capsuleLayout("Wiki", measure);
    const tall = capsuleLayout("The Hitchhikers Guide To The Galaxy Wiki", measure);
    expect(tall.mark.y).toBeLessThan(short.mark.y);

    for (const layout of [short, tall]) {
      const top = layout.mark.y;
      const bottom = layout.textTop + layout.textHeight;
      expect(Math.abs((top + bottom) / 2 - CAPSULE_H / 2)).toBeLessThanOrEqual(1);
    }
  });

  it("a nameless page still centres its mark", () => {
    const layout = capsuleLayout("", measure);
    expect(Math.abs(layout.mark.y + layout.mark.size / 2 - CAPSULE_H / 2)).toBeLessThanOrEqual(1);
  });
});
