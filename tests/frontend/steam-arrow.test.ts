import { describe, expect, it } from "vitest";

import { steamDrawsArrow } from "../../src/lib/steamArrow";

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
