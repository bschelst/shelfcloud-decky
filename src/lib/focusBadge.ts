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

/** Page art, as opposed to card art.
 *
 *  A card shows a capsule — <id>p.png or <id>.jpg. A PAGE shows <id>_hero.jpg
 *  and <id>_logo.png. Both carry an app id, and telling them apart is what
 *  stops this badging the wrong thing: focus the Play button on a game page
 *  and the search upwards would otherwise find the hero behind it and pin a
 *  card badge to the page, beside the React one already there. */
const PAGE_ART = /_(?:hero|logo)\./;

/** How far to look upwards for the card holding this art.
 *
 *  The focused element is usually the card itself, but not always — a result
 *  in the search overlay can be an inner control. Two levels covers that;
 *  more would start finding the grid. */
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

/**
 * The card the focus is on, and which game it is — or null for neither.
 *
 * The focused element is usually the card itself. In the search overlay it
 * can be an inner control with the art a level or two above, so this looks
 * upward as well — but only a little, and only at card art. Without the
 * PAGE_ART rule, focusing Play on a game page would walk up, find the hero
 * behind it and pin a second badge to the page beside the real one.
 */
function focusedCard(doc: Document): { card: Element; appId: string } | null {
  let element: Element | null = doc.querySelector(`.${FOCUS_CLASS}`);
  for (let step = 0; element && step <= ANCESTORS_TO_TRY; step++) {
    const appId = appIdWithin(element);
    if (appId) return { card: element, appId };
    element = element.parentElement;
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
  const found = focusedCard(doc);
  const card = found?.card ?? null;
  if (card && card === lastCard && card.querySelector(`.${BADGE_CLASS}`)) return;

  clear(doc);
  if (!found) return;

  const { appId } = found;
  const provider = providers[appId];
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
