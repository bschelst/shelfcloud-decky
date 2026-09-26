// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What a provider is CALLED, everywhere a person can read it.
//
// `providerLabel` falls back to the raw key when a provider is missing from
// the table, which means a miss does not fail — it ships. types.ts says so in
// a comment, and KNOWN_PROVIDERS was exported "for tests that check nothing is
// missed". No such test was ever written, and two providers were missing for
// as long as they existed:
//
//   * a Steam tile titled "Shelfcloud Website (shelfcloud)"
//   * a Steam collection called, literally, "custom (Shelfcloud)"
//
// These are the tests that were supposed to exist. They assert the SHAPE of a
// name rather than only listing the current ones, so a provider added next
// year is caught by the same rule rather than needing a line here.

import { describe, expect, it, vi } from "vitest";

// sync.ts reaches @decky/api through lib/api, which does not resolve outside
// the Steam client. Only the names are under test here, so the boundary is
// stubbed rather than the whole module tree pulled in.
vi.mock("../../src/lib/api", () => ({ layoutSelectionBase: vi.fn(async () => ({ ok: false })), logNote: vi.fn(async () => ({ ok: true })),
  fetchArtwork: vi.fn(async () => ({ ok: false })),
  saveIcon: vi.fn(async () => ({ ok: false })),
  strayShortcuts: vi.fn(async () => []),
}));

import { collectionName } from "../../src/lib/collection";
import { shortcutTitle } from "../../src/lib/sync";
import {
  KNOWN_PROVIDERS, WEBSITE_PROVIDERS, providerLabel, type DesiredGame,
} from "../../src/lib/types";

const game = (over: Partial<DesiredGame> = {}): DesiredGame => ({
  game_id: 1,
  name: "Fortnite",
  provider: "boosteroid",
  exe: "/usr/bin/python3",
  options: "--game-id 1",
  ...over,
} as DesiredGame);

/** A raw provider key: all lower case, often with an underscore. No label
 *  should ever look like this — that is what the fallback produces. */
const looksLikeAKey = (value: string) =>
  value.includes("_") || (value === value.toLowerCase() && value !== "");

describe("every provider has a name written for a person", () => {
  it.each(KNOWN_PROVIDERS)("%s does not fall through to its key", (provider) => {
    expect(providerLabel(provider)).not.toBe(provider);
  });

  it.each(KNOWN_PROVIDERS)("%s is not named like a key", (provider) => {
    expect(looksLikeAKey(providerLabel(provider))).toBe(false);
  });

  it("includes the two website providers, which were the ones missed", () => {
    // Named explicitly as well as covered by the sweep above: they are the
    // reason this file exists, and a future tidy-up that drops them from the
    // table would otherwise just shrink the sweep and pass.
    expect(KNOWN_PROVIDERS).toContain("shelfcloud");
    expect(KNOWN_PROVIDERS).toContain("custom");
  });
});

describe("collection names", () => {
  it.each(KNOWN_PROVIDERS)("%s produces a readable collection", (provider) => {
    const name = collectionName(provider);
    expect(name.endsWith(" (Shelfcloud)")).toBe(true);
    expect(looksLikeAKey(name.replace(" (Shelfcloud)", ""))).toBe(false);
  });

  it("gives each kind of web page its OWN collection", () => {
    // Merging them was tried and was wrong: it put the page Shelfcloud
    // publishes in with the ones you typed in yourself, and left the
    // collection named after its own provider standing empty.
    const names = [...WEBSITE_PROVIDERS].map(collectionName);
    expect(new Set(names).size).toBe(WEBSITE_PROVIDERS.size);
    expect(collectionName("shelfcloud")).toBe("Shelfcloud (Shelfcloud)");
    expect(collectionName("custom")).toBe("Websites (Shelfcloud)");
  });

  it("does not leave a provider's own collection empty by filing it elsewhere", () => {
    // The shape of the bug, stated as a rule: every provider's tiles go to
    // the collection named after that provider, with no exceptions.
    for (const provider of KNOWN_PROVIDERS) {
      expect(collectionName(provider)).toBe(`${providerLabel(provider)} (Shelfcloud)`);
    }
  });

  it("still separates the cloud services from each other", () => {
    expect(collectionName("boosteroid")).toBe("Boosteroid (Shelfcloud)");
    expect(collectionName("geforce_now")).toBe("GeForce NOW (Shelfcloud)");
    expect(collectionName("boosteroid")).not.toBe(collectionName("geforce_now"));
  });
});

describe("tile titles", () => {
  it("suffixes a cloud game with its provider", () => {
    // The reason the suffix exists: a streamed copy sitting next to an
    // installed one under one name.
    expect(shortcutTitle(game())).toBe("Fortnite (Boosteroid)");
  });

  it("leaves a web page alone", () => {
    expect(shortcutTitle(game({ name: "Shelfcloud Website", provider: "shelfcloud" })))
      .toBe("Shelfcloud Website");
    expect(shortcutTitle(game({ name: "Schelstraete Bart", provider: "custom" })))
      .toBe("Schelstraete Bart");
  });

  it("never puts a raw key in a title", () => {
    for (const provider of KNOWN_PROVIDERS) {
      expect(shortcutTitle(game({ provider }))).not.toContain(provider);
    }
  });
});
