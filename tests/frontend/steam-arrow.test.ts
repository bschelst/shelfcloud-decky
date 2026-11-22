import { describe, expect, it } from "vitest";

import { ARROW_ICON, ARROW_WIDTH, CLASS_RETRY_MS, fallbackArrowStyle, PLAY_SQUARED, shouldLookAgain, steamDrawsArrow } from "../../src/lib/steamArrow";

const show = () => undefined;              // the Play bar's ShowStreamingMenu
const el = (props: Record<string, unknown>) => ({ type: "x", props });
const row = (...children: unknown[]) => el({ className: "row", children });

const play = el({ onClick: () => undefined, className: "play" });
const explainer = el({ overview: {}, active: false, onDismiss: () => undefined });

describe("steamDrawsArrow", () => {
  it("sees Steam's arrow by its click handler", () => {
    expect(steamDrawsArrow(row(play, false, el({ overview: {}, onClick: show }), explainer), show)).toBe(true);
  });

  it("sees it in the mouse-up mode too", () => {
    expect(steamDrawsArrow(row(play, el({ overview: {}, onMouseUp: show }), explainer), show)).toBe(true);
  });

  it("knows when the slot is empty, as on the Deck with the streaming PC off", () => {
    // [Play, false, false, explainer] is what Steam rendered for Dead by Daylight
    expect(steamDrawsArrow(row(play, false, false, explainer), show)).toBe(false);
  });

  it("does not take the explainer or Play for an arrow", () => {
    expect(steamDrawsArrow(row(play, explainer), show)).toBe(false);
  });

  it("copes with odd shapes", () => {
    for (const odd of [null, undefined, false, "text", el({}), el({ children: "x" }), row()]) {
      expect(steamDrawsArrow(odd, show)).toBe(false);
    }
    expect(steamDrawsArrow(row(play, el({ onClick: show })), undefined)).toBe(false);
  });
});

// Decky 4 on a Steam beta did not have the arrow classes indexed. Then we draw
// the arrow ourself, and keep asking Decky now and then in case it gets them.
describe("when Steam's arrow classes are missing", () => {
  it("looks the first time, then not again before the wait is over", () => {
    expect(shouldLookAgain(0, 1000)).toBe(true);
    expect(shouldLookAgain(1000, 1000 + CLASS_RETRY_MS - 1)).toBe(false);
    expect(shouldLookAgain(1000, 1000 + CLASS_RETRY_MS)).toBe(true);
  });

  it("our arrow looks like Steam's: narrow, flush, small triangle", () => {
    const idle = fallbackArrowStyle(false);
    const focused = fallbackArrowStyle(true);
    expect(idle.height).toBe(48);
    expect(idle.width).toBe(ARROW_WIDTH);
    expect(ARROW_WIDTH).toBeLessThan(30);
    expect(ARROW_ICON).toBe(12);
    expect(idle.margin).toBe(0);                     // no gap after Play
    expect(idle.borderRadius).toBe("0 2px 2px 0");   // round on the outside only
    expect(focused.background).toBe("rgb(255, 255, 255)");
    expect(focused.color).toBe("rgb(14, 20, 27)");
    expect(PLAY_SQUARED).toEqual({ borderTopRightRadius: 0, borderBottomRightRadius: 0 });
  });
});
