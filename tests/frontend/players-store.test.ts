import { describe, expect, it } from "vitest";

import {
  cachedPlayers, clearPlayers, rememberPlayers, PLAYERS_MAX_AGE_MS, PLAYERS_MAX_ENTRIES, PLAYERS_STORE_KEY,
} from "../../src/lib/playersStore";

/** A localStorage stand-in. */
function memoryStore(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); },
    raw: () => data.get(PLAYERS_STORE_KEY),
  };
}

const T0 = 1_800_000_000_000;

describe("players store", () => {
  it("gives back a count it was given", () => {
    const store = memoryStore();
    rememberPlayers("2206769278", 54946, T0, store);
    expect(cachedPlayers("2206769278", T0 + 1000, store)).toBe(54946);
  });

  it("knows nothing about a tile it never saw", () => {
    expect(cachedPlayers("123", T0, memoryStore())).toBeUndefined();
  });

  it("forgets a count older than two hours", () => {
    const store = memoryStore();
    rememberPlayers("1", 10, T0, store);
    expect(cachedPlayers("1", T0 + PLAYERS_MAX_AGE_MS - 1, store)).toBe(10);
    expect(cachedPlayers("1", T0 + PLAYERS_MAX_AGE_MS + 1, store)).toBeUndefined();
  });

  it("keeps a zero, nobody playing is an answer", () => {
    const store = memoryStore();
    rememberPlayers("1", 0, T0, store);
    expect(cachedPlayers("1", T0, store)).toBe(0);
  });

  it("refuses counts that are not a real count", () => {
    const store = memoryStore();
    for (const bad of [-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      rememberPlayers("1", bad, T0, store);
      expect(cachedPlayers("1", T0, store)).toBeUndefined();
    }
  });

  it("survives junk in storage", () => {
    for (const junk of ["not json", "[]", "null", '{"1": "x"}', '{"1": {"n": "5", "at": 1}}']) {
      const store = memoryStore({ [PLAYERS_STORE_KEY]: junk });
      expect(cachedPlayers("1", T0, store)).toBeUndefined();
      rememberPlayers("2", 3, T0, store);            // and can still write
      expect(cachedPlayers("2", T0, store)).toBe(3);
    }
  });

  it("drops old entries and stays under the cap", () => {
    const store = memoryStore();
    rememberPlayers("old", 1, T0 - PLAYERS_MAX_AGE_MS - 1, store);
    for (let i = 0; i < PLAYERS_MAX_ENTRIES + 50; i++) rememberPlayers(String(i), i, T0 + i, store);
    const saved = JSON.parse(store.raw() ?? "{}");
    expect(Object.keys(saved).length).toBeLessThanOrEqual(PLAYERS_MAX_ENTRIES);
    expect(saved.old).toBeUndefined();
    // the newest ones are the ones kept
    expect(cachedPlayers(String(PLAYERS_MAX_ENTRIES + 49), T0 + PLAYERS_MAX_ENTRIES + 49, store))
      .toBe(PLAYERS_MAX_ENTRIES + 49);
  });

  it("works without any storage at all", () => {
    rememberPlayers("1", 5, T0, null);
    expect(cachedPlayers("1", T0, null)).toBeUndefined();
  });

  it("does not throw when storage refuses to write", () => {
    const full = { getItem: () => null, setItem: () => { throw new Error("QuotaExceededError"); } };
    expect(() => rememberPlayers("1", 5, T0, full)).not.toThrow();
  });
});

describe("clearPlayers", () => {
  it("forgets every stored count", () => {
    const store = memoryStore();
    rememberPlayers("1", 5, T0, store);
    rememberPlayers("2", 6, T0, store);
    expect(clearPlayers(store)).toBe(true);
    expect(cachedPlayers("1", T0, store)).toBeUndefined();
    expect(cachedPlayers("2", T0, store)).toBeUndefined();
  });

  it("says so when storage refuses", () => {
    const stuck = { getItem: () => null, setItem: () => { throw new Error("denied"); } };
    expect(clearPlayers(stuck)).toBe(false);
  });

  it("is fine without storage", () => {
    expect(clearPlayers(null)).toBe(true);
  });
});
