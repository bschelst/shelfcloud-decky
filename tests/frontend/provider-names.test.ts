// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What a provider is CALLED, everywhere a person can read it.
//
// providerLabel falls back to the raw key when a provider is missing from the
// table, so a miss ships instead of failing. Two providers were missing for as
// long as they existed. These tests assert the SHAPE of a name, so a new
// provider is caught by the same rule.

import { describe, expect, it, vi } from "vitest";

// lib/api does not resolve outside the Steam client. Only names are tested
// here, so the boundary is stubbed
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
    // named explicitely too: a future tidy-up dropping them would otherwise just
    // shrink the sweep and pass.
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
    // merging them was wrong: it mixed the published page with hand-added ones
    // and left a provider's own collection empty.
    const names = [...WEBSITE_PROVIDERS].map(collectionName);
    expect(new Set(names).size).toBe(WEBSITE_PROVIDERS.size);
    expect(collectionName("shelfcloud")).toBe("Shelfcloud (Shelfcloud)");
    expect(collectionName("custom")).toBe("Websites (Shelfcloud)");
  });

  it("does not leave a provider's own collection empty by filing it elsewhere", () => {
    // the rule: every provider its tiles go to the collection named after it.
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
    // suffix exists so a streamed copy is not confused with an installed one.
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
