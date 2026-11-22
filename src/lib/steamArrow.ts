// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Does Steam draw its own Play dropdown arrow in this render? Steam's Play bar
// renders the arrow as `show && <Arrow onClick={this.ShowStreamingMenu} />`, a
// child of the row, and `false` in that slot when there is nothing to stream
// from. The prop bShowStreamingSelector does NOT tell: it stays true with the
// streaming PC switched off, and then there was neither Steam's arrow nor ours.
// So we look for the child that opens the streaming menu. No React import, so
// it can be tested.

interface Elementish { props?: { children?: unknown; onClick?: unknown; onMouseUp?: unknown } }

function asElement(value: unknown): Elementish | null {
  return typeof value === "object" && value !== null && "props" in value ? value as Elementish : null;
}

/** How long a failed lookup of Steam's arrow classes is believed. Not for ever:
 *  Decky 4 on a Steam beta did not have the module indexed at all, and an index
 *  that gets it later should be used then. A few seconds keeps the search off
 *  every single render. */
export const CLASS_RETRY_MS = 5000;

/** Look again for the classes? `lastMiss` is when it last failed, 0 for never. */
export function shouldLookAgain(lastMiss: number, now: number): boolean {
  return lastMiss === 0 || now - lastMiss >= CLASS_RETRY_MS;
}

/** Our own look for the arrow when Decky can not give Steam's classes. Taken
 *  from Steam's own rules for its arrow (read on the Deck, which still has
 *  them): a narrow strip right against Play, only the right corners round, a
 *  12px triangle, white with a dark triangle on focus. The 24px width is
 *  measured off a photo of Steam's arrow, Steam sets it in a rule without a name. */
export const ARROW_WIDTH = 24;
export const ARROW_ICON = 12;
export const PLAY_HEIGHT = 48;

export function fallbackArrowStyle(focused: boolean): Record<string, string | number> {
  return {
    display: "flex", alignItems: "center", justifyContent: "center",
    alignSelf: "center", height: PLAY_HEIGHT, width: ARROW_WIDTH, flex: "none",
    margin: 0, padding: 0, borderRadius: "0 2px 2px 0", boxSizing: "border-box",
    cursor: "pointer",
    background: focused ? "rgb(255, 255, 255)" : "rgba(220, 222, 223, 0.17)",
    color: focused ? "rgb(14, 20, 27)" : "rgb(220, 222, 223)",
  };
}

/** Play's own corners on the arrow side go square, as Steam does it with its
 *  arrow (".ShowingStreaming .PlayButton { border-radius: 2px 0 0 2px }"), so
 *  the two read as one button. */
export const PLAY_SQUARED: Record<string, string | number> = {
  borderTopRightRadius: 0, borderBottomRightRadius: 0,
};

/** True when the row `rendered` has a child wired to `showStreamingMenu`. */
export function steamDrawsArrow(rendered: unknown, showStreamingMenu: unknown): boolean {
  if (typeof showStreamingMenu !== "function") return false;
  const children = asElement(rendered)?.props?.children;
  const list = Array.isArray(children) ? children.flat(2) : [children];
  return list.some((child) => {
    const props = asElement(child)?.props;
    return props?.onClick === showStreamingMenu || props?.onMouseUp === showStreamingMenu;
  });
}
