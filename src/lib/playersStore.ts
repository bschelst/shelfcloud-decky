// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The last player count per tile, kept in Steam's localStorage. So a game page
// shows a number on its first frame, also after a Steam restart, and then the
// fresh count replaces it. Same idea as the ProtonDB plugin's cache. Plain
// localStorage and not localforage: it reads synchronous, so there is no frame
// with a dash first, and it needs no extra package.
// No @decky/ui in here so it can be tested.

/** One key for the lot, our name on it. Steam's storage is shared by every plugin. */
export const PLAYERS_STORE_KEY = "shelfcloud-players-v1";

/** Older than this is not "playing right now" anymore. Same as the backend. */
export const PLAYERS_MAX_AGE_MS = 2 * 60 * 60 * 1000;

/** A library has some hundreds of tiles. A cap so it can never grow unbound. */
export const PLAYERS_MAX_ENTRIES = 1000;

/** The part of localStorage we use, so tests can hand in their own. */
export interface SimpleStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface Entry { n: number; at: number }

function defaultStore(): SimpleStore | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    // some contexts throw on the access itself
    return null;
  }
}

function isCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isEntry(value: unknown): value is Entry {
  const entry = value as Entry | null;
  return typeof entry === "object" && entry !== null
    && isCount(entry.n) && typeof entry.at === "number" && Number.isFinite(entry.at);
}

/** Everything stored, with anything malformed left out. Never throws. */
function load(store: SimpleStore): Record<string, Entry> {
  try {
    const parsed: unknown = JSON.parse(store.getItem(PLAYERS_STORE_KEY) ?? "{}");
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, v]) => isEntry(v))) as Record<string, Entry>;
  } catch {
    return {};
  }
}

/** The stored count for a tile, if there is one younger than two hours. */
export function cachedPlayers(
  appId: string, now: number = Date.now(), store: SimpleStore | null = defaultStore(),
): number | undefined {
  if (!store || !appId) return undefined;
  const entry = load(store)[appId];
  if (!entry || now - entry.at > PLAYERS_MAX_AGE_MS) return undefined;
  return entry.n;
}

/** Store a fresh count. Old entries go, and only the newest PLAYERS_MAX_ENTRIES
 *  stay. A storage that refuses (full, private) is ignored: this is a nicety. */
export function rememberPlayers(
  appId: string, count: number, now: number = Date.now(), store: SimpleStore | null = defaultStore(),
): void {
  if (!store || !appId || !isCount(count)) return;
  const kept = Object.entries({ ...load(store), [appId]: { n: count, at: now } })
    .filter(([, entry]) => now - entry.at <= PLAYERS_MAX_AGE_MS)
    .sort(([, a], [, b]) => b.at - a.at)
    .slice(0, PLAYERS_MAX_ENTRIES);
  try {
    store.setItem(PLAYERS_STORE_KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch {
    // full or not allowed, the page just asks the backend like before
  }
}

/** Forget every stored count, for the Clear cache button. False when the
 *  storage refused, so the button can say it did not work. */
export function clearPlayers(store: SimpleStore | null = defaultStore()): boolean {
  if (!store) return true;   // nothing stored, nothing to clear
  try {
    store.setItem(PLAYERS_STORE_KEY, "{}");
    return true;
  } catch {
    return false;
  }
}
