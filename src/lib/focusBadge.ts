// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The provider mark on the FOCUSED card, on the home page and the grid.
//
// The game page gets its badge by patching React; this cannot. The home
// carousel is a virtual grid with reused cards and no stable component to hang
// a child off. The ProtonDB library dots hit the same wall and answer it the
// same way, put an element in the document, with two non-obvious bits:
//   - the library lives in the BIG PICTURE window, a different document, so it
//     comes from SteamUIStore, not the global `document`.
//   - the app id is in the card image URL, /customimages/<appid>p.png.
//
// Only the focused card is marked: one mark you can read beats forty you cannot.

import { providerInfo } from "./theme";

/** Steam's class for the card that currently has focus. */
const FOCUS_CLASS = "gpfocus";

/** Ours, so it can be found again and removed. */
const BADGE_CLASS = "shelfcloud-focus-badge";

/** appid out of /assets/<id>... or /customimages/<id>... */
const APP_ID_IN_SRC = /\/(?:assets|customimages)\/(\d+)/;

/** Page art, not card art. A card shows <id>p.png or <id>.jpg, a PAGE shows
 *  <id>_hero.jpg and <id>_logo.png. Telling them apart stops us badging the
 *  hero behind a game page's Play button. */
const PAGE_ART = /_(?:hero|logo)\./;

/** How far to look up for the card holding this art. Two levels covers a
 *  search-overlay result being an inner control; more would find the grid. */
const ANCESTORS_TO_TRY = 2;

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

/** The app id of the card art inside this element, ignoring page art. */
function appIdWithin(element: Element): string | null {
  for (const img of element.querySelectorAll("img")) {
    const src = (img as HTMLImageElement).src || "";
    if (PAGE_ART.test(src)) continue;
    const found = src.match(APP_ID_IN_SRC);
    if (found) return found[1];
  }
  return null;
}

/** The card the focus is on and which game it is, or null. Looks up a little too,
 *  since a search result can be an inner control. Card art only, not page heroes. */
function focusedCard(doc: Document): { card: Element; appId: string } | null {
  let element: Element | null = doc.querySelector(`.${FOCUS_CLASS}`);
  for (let step = 0; element && step <= ANCESTORS_TO_TRY; step++) {
    const appId = appIdWithin(element);
    if (appId) return { card: element, appId };
    element = element.parentElement;
  }
  return null;
}

/** The provider mark, as plain DOM. Built by hand, not React: a React root in
 *  another window per focus change would cost far more. Same as the ProtonDB dot. */
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
  const found = focusedCard(doc);
  const card = found?.card ?? null;
  if (card && card === lastCard && card.querySelector(`.${BADGE_CLASS}`)) return;

  clear(doc);
  if (!found) return;

  const { appId } = found;
  const provider = providers[appId];
  // not one of ours, which is most cards. drawing nothing is the hole point.
  if (!provider) return;

  const badge = buildBadge(doc, provider);
  if (position === "tl" || position === "tr" || position === "tm") {
    badge.style.bottom = "";
    badge.style.top = "8px";
  }
  if (position === "tr" || position === "br") {
    badge.style.left = "";
    badge.style.right = "8px";
  }
  // Centred last, so it wins over whichever side the corner rules set. 8px is
  // the inset everywhere here -- a card has no furniture, so no special drop.
  if (position === "tm" || position === "bm") {
    badge.style.right = "";
    badge.style.left = "50%";
    badge.style.transform = "translateX(-50%)";
  }

  // the card must be a containing block, else the absolute child slips out to
  // the whole grid.
  const host = card as HTMLElement;
  const view = doc.defaultView;
  if (view && view.getComputedStyle(host).position === "static") {
    host.style.position = "relative";
  }
  host.appendChild(badge);
  lastCard = card;
}

/** Follow the focused card and keep the badge on it. Driven by a class-attribute
 *  observer, not a timer: gpfocus moves on the moment the stick does. */
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
