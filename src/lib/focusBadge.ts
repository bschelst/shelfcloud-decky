// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The provider mark on the HIGHLIGHTED card, on the home page and the grid.
//
// The game page gets its badge by patching React (see patchLibraryApp). This
// cannot: the home carousel is a virtualised grid whose cards are recycled as
// you scroll, and there is no stable component to hang a child off. The
// ProtonDB plugin meets the same wall with its library dots and answers it the
// same way — reach into the document and put an element there — so this
// follows that, including the two things about it that are not obvious:
//
//   - The library is drawn in the BIG PICTURE window, a different document
//     from the one this plugin's code runs in. Reaching for the global
//     `document` finds an empty page and nothing ever appears. It has to come
//     from SteamUIStore.
//
//   - The app id is not in a data attribute. It is in the card's image URL,
//     /customimages/<appid>p.png — which is also how ProtonDB finds it.
//
// Only the focused card is marked, which is what was asked for and is also
// the honest thing: one mark you can read beats forty you cannot.

import { providerInfo } from "./theme";

/** Steam's class for the card that currently has focus. */
const FOCUS_CLASS = "gpfocus";

/** Ours, so it can be found again and removed. */
const BADGE_CLASS = "shelfcloud-focus-badge";

/** appid out of /assets/<id>... or /customimages/<id>... */
const APP_ID_IN_SRC = /\/(?:assets|customimages)\/(\d+)/;

declare const SteamUIStore: {
  WindowStore?: {
    GamepadUIMainWindowInstance?: { m_BrowserWindow?: { document?: Document } };
  };
};

function bigPictureDocument(): Document | null {
  try {
    return SteamUIStore?.WindowStore?.GamepadUIMainWindowInstance
      ?.m_BrowserWindow?.document ?? null;
  } catch {
    return null;
  }
}

function appIdOf(card: Element): string | null {
  for (const img of card.querySelectorAll("img")) {
    const found = ((img as HTMLImageElement).src || "").match(APP_ID_IN_SRC);
    if (found) return found[1];
  }
  return null;
}

/** The provider mark, as plain DOM.
 *
 *  Built by hand rather than rendered with React: this lands in another
 *  window's document, and mounting a React root per focus change — which on a
 *  carousel is every few hundred milliseconds — would cost far more than the
 *  handful of nodes it draws. Same reasoning as the ProtonDB dot.
 */
function buildBadge(doc: Document, provider: string): HTMLElement {
  const { label, color } = providerInfo(provider);

  const pill = doc.createElement("div");
  pill.className = BADGE_CLASS;
  Object.assign(pill.style, {
    position: "absolute",
    left: "8px",
    bottom: "8px",
    zIndex: "20",
    display: "flex",
    alignItems: "center",
    gap: "5px",
    padding: "3px 8px",
    borderRadius: "999px",
    background: "rgba(8, 10, 14, 0.82)",
    border: `1px solid ${color}`,
    color: "#e8ecf2",
    font: "600 11px/1 system-ui, sans-serif",
    whiteSpace: "nowrap",
    pointerEvents: "none",
    backdropFilter: "blur(6px)",
  } as Partial<CSSStyleDeclaration>);

  const dot = doc.createElement("span");
  Object.assign(dot.style, {
    width: "7px",
    height: "7px",
    borderRadius: "50%",
    background: color,
    flex: "0 0 auto",
  } as Partial<CSSStyleDeclaration>);

  pill.appendChild(dot);
  pill.appendChild(doc.createTextNode(label));
  return pill;
}

let observer: MutationObserver | null = null;
let lastCard: Element | null = null;

/** Take the badge off wherever it is. */
function clear(doc: Document): void {
  for (const old of doc.querySelectorAll(`.${BADGE_CLASS}`)) old.remove();
  lastCard = null;
}

function update(doc: Document, providers: Record<string, string>,
                position: string): void {
  const card = doc.querySelector(`.${FOCUS_CLASS}`);
  if (card === lastCard && card?.querySelector(`.${BADGE_CLASS}`)) return;

  clear(doc);
  if (!card) return;

  const appId = appIdOf(card);
  const provider = appId ? providers[appId] : undefined;
  // Not one of ours, which is most cards. Nothing is drawn, and that absence
  // is what makes the badge mean "this one streams".
  if (!provider) return;

  const badge = buildBadge(doc, provider);
  if (position === "tl" || position === "tr") {
    badge.style.bottom = "";
    badge.style.top = "8px";
  }
  if (position === "tr" || position === "br") {
    badge.style.left = "";
    badge.style.right = "8px";
  }

  // The card has to be a containing block, or an absolutely positioned child
  // escapes to the nearest one — which on this page is the whole grid.
  const host = card as HTMLElement;
  const view = doc.defaultView;
  if (view && view.getComputedStyle(host).position === "static") {
    host.style.position = "relative";
  }
  host.appendChild(badge);
  lastCard = card;
}

/**
 * Follow the focused card and keep the badge on it.
 *
 * Driven by a mutation observer on the class attribute rather than a timer:
 * `gpfocus` moves the moment the stick does, and a poll slow enough not to
 * waste power would visibly lag behind the highlight.
 */
export function startFocusBadge(
  getData: () => { providers: Record<string, string>; enabled: boolean; position: string },
): void {
  stopFocusBadge();
  const doc = bigPictureDocument();
  if (!doc?.body) return;

  const run = () => {
    const { providers, enabled, position } = getData();
    if (!enabled) { clear(doc); return; }
    update(doc, providers, position);
  };

  observer = new MutationObserver(run);
  observer.observe(doc.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ["class"],
  });
  run();
}

export function stopFocusBadge(): void {
  observer?.disconnect();
  observer = null;
  const doc = bigPictureDocument();
  if (doc) clear(doc);
}
