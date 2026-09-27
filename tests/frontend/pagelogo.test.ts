// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Setting a page's name as its logo. Steam draws the logo slot instead of the
// title, and every page got the same Shelfcloud wordmark, so a shelf of them
// all read "Shelfcloud". There is no canvas here, so the layout is a pure
// function taking a measuring callback -- and that is where the bugs are: Steam
// clips an overflowing logo with no warning.

import { describe, expect, it } from "vitest";

import { logoLayout, LOGO_W, LOGO_H } from "../../src/lib/pagelogo";
import { ellipsise } from "../../src/lib/textfit";

/** Stand-in for canvas text measurement: every glyph is 0.55em. The arithmetic
 *  around the measurement is what is under test, not a real font. */
const measure = (text: string, size: number) => text.length * size * 0.55;

const widthOf = (layout: { lines: string[]; size: number }) =>
  Math.max(0, ...layout.lines.map((line) => measure(line, layout.size)));

describe("fitting the name", () => {
  it("sets a short name large", () => {
    const layout = logoLayout("Wiki", measure);
    expect(layout.lines).toEqual(["Wiki"]);
    expect(layout.size).toBeGreaterThanOrEqual(56);
  });

  it("drops to a smaller size before it wraps", () => {
    const short = logoLayout("Wiki", measure);
    const long = logoLayout("Schelstraete Bart", measure);
    expect(long.size).toBeLessThan(short.size);
  });

  it("wraps onto a second line rather than shrinking to nothing", () => {
    const layout = logoLayout("The Hitchhikers Guide To The Galaxy Wiki", measure);
    expect(layout.lines.length).toBeGreaterThan(1);
    expect(layout.lines.length).toBeLessThanOrEqual(2);
    expect(layout.lines.join(" ")).toBe("The Hitchhikers Guide To The Galaxy Wiki");
  });

  it("never lets the words run past the canvas", () => {
    // Steam clips with no warning, leaving the name half there.
    for (const name of [
      "Wiki",
      "Schelstraete Bart",
      "The Hitchhikers Guide To The Galaxy Wiki",
      "Supercalifragilisticexpialidocious",
      "A".repeat(200),
      "word ".repeat(60),
    ]) {
      const layout = logoLayout(name, measure);
      expect(widthOf(layout)).toBeLessThanOrEqual(layout.textWidth + 0.5);
      expect(layout.textLeft + layout.textWidth).toBeLessThanOrEqual(LOGO_W);
    }
  });

  it("truncates a single unbreakable word instead of overflowing", () => {
    const layout = logoLayout("Supercalifragilisticexpialidocious".repeat(4), measure);
    expect(layout.lines).toHaveLength(1);
    expect(layout.lines[0].endsWith("…")).toBe(true);
    expect(widthOf(layout)).toBeLessThanOrEqual(layout.textWidth + 0.5);
  });

  it("fills the canvas rather than floating in the middle of it", () => {
    // Steam pins the logo against the bottom of the hero, so empty canvas below
    // becomes a gap and the logo rides high (WALL-E only reached 71%). Real
    // logos fill roughly 20%..85% of the height, so leave at most 20% empty.
    const layout = logoLayout("Anything", measure);
    const bottom = layout.mark.y + layout.mark.size;
    expect(bottom / LOGO_H).toBeGreaterThan(0.8);
    // symmetric: the mark is centred, so the same holds above it.
    expect(layout.mark.y / LOGO_H).toBeLessThan(0.2);
  });

  it("centres the mark and words as one group in the canvas", () => {
    // the mark used to be pinned left, so a short name sat off to one side and
    // the provider chip floated wrong. A name that fits should sit dead centre.
    const layout = logoLayout("Wiki", measure);
    const left = layout.mark.x;
    const right = layout.textLeft + layout.textWidth;
    expect((left + right) / 2).toBeCloseTo(LOGO_W / 2, 0);
  });

  it("keeps the mark inside the canvas and clear of the words", () => {
    const layout = logoLayout("Anything", measure);
    expect(layout.mark.x).toBeGreaterThanOrEqual(0);
    expect(layout.mark.y).toBeGreaterThanOrEqual(0);
    expect(layout.mark.y + layout.mark.size).toBeLessThanOrEqual(LOGO_H);
    expect(layout.mark.x + layout.mark.size).toBeLessThanOrEqual(layout.textLeft);
  });
});

describe("names that are barely names", () => {
  it("an empty name draws no words rather than an empty box", () => {
    for (const name of ["", "   ", "\n\t"]) {
      expect(logoLayout(name, measure).lines).toEqual([]);
    }
  });

  it("collapses runs of whitespace", () => {
    const layout = logoLayout("  My    Wiki  ", measure);
    expect(layout.lines.join(" ")).toBe("My Wiki");
  });

  it("survives a name that is only punctuation", () => {
    const layout = logoLayout("!!!", measure);
    expect(layout.lines).toEqual(["!!!"]);
  });

  it("survives a name of emoji", () => {
    const layout = logoLayout("🎮🎮🎮", measure);
    expect(widthOf(layout)).toBeLessThanOrEqual(layout.textWidth + 0.5);
  });
});

describe("ellipsise", () => {
  it("leaves something that already fits alone", () => {
    expect(ellipsise("short", 1000, 20, measure)).toBe("short");
  });

  it("always fits what it returns", () => {
    for (const width of [40, 80, 160, 320]) {
      const cut = ellipsise("a very long name indeed that will not fit", width, 30, measure);
      expect(measure(cut, 30)).toBeLessThanOrEqual(width + 0.5);
    }
  });

  it("does not loop forever on a width nothing fits in", () => {
    const cut = ellipsise("anything", 1, 40, measure);
    expect(typeof cut).toBe("string");
    expect(cut.endsWith("…")).toBe(true);
  });
});
