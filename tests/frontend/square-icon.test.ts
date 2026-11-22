import { describe, expect, it } from "vitest";

import { ICON_SIZE, squareCrop } from "../../src/lib/squareicon";

describe("squareCrop", () => {
  it("takes the middle of a tall capsule", () => {
    // Steam's 600x900 capsule: the full width, centred top to bottom
    expect(squareCrop(600, 900)).toEqual({ sx: 0, sy: 150, size: 600 });
  });

  it("takes the middle of a wide picture", () => {
    expect(squareCrop(920, 430)).toEqual({ sx: 245, sy: 0, size: 430 });
  });

  it("keeps a square as it is", () => {
    expect(squareCrop(256, 256)).toEqual({ sx: 0, sy: 0, size: 256 });
  });

  it("refuses sizes that are no picture", () => {
    for (const [w, h] of [[0, 900], [600, 0], [-1, 5], [Number.NaN, 5], [5, Number.POSITIVE_INFINITY]]) {
      expect(squareCrop(w, h)).toBeUndefined();
    }
  });

  it("draws icons at Steam's size", () => {
    expect(ICON_SIZE).toBe(256);
  });
});
