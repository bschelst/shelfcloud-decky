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
