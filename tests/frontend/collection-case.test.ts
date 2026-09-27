// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A collection that vanished and came back every few minutes.
//
// Two collections one capital letter apart took turns, each cycle minting a
// new id. The id matters: shortcut membership is stored locally keyed by it,
// so a new id loses the members.
//
// Three places compared the name exactly and broke on the mismatch: filing
// made a second collection, retiring deleted it, pruning emptied it.

import { beforeEach, describe, expect, it } from "vitest";

import {
  addToProviderCollections,
  pruneProviderCollections,
  removeRetiredCollections,
} from "../../src/lib/collection";

// Shaped like the real thing on purpose: a collection has NO SetName, only a
// displayName accessor. A first fix called SetName and silently did nothing;
// a fake with SetName would have let that ship.
type FakeCollection = {
  id: string;
  displayName: string;
  allApps: Array<{ appid: number }>;
  deleted: boolean;
  added: number[];
  removedApps: number[];
  saves: number;
  Delete: () => Promise<void>;
  RemoveApps: (apps: Array<{ appid: number }>) => void;
  AsDragDropCollection: () => { AddApps: (apps: Array<{ appid: number }>) => void };
  Save: () => Promise<void>;
};

function collection(name: string, appIds: number[] = []): FakeCollection {
  const self: FakeCollection = {
    id: `id-of-${name}`,
    displayName: name,
    allApps: appIds.map((appid) => ({ appid })),
    deleted: false,
    added: [],
    removedApps: [],
    saves: 0,
    Delete: async () => { self.deleted = true; },
    RemoveApps: (apps) => { self.removedApps.push(...apps.map((a) => a.appid)); },
    AsDragDropCollection: () => ({
      AddApps: (apps) => { self.added.push(...apps.map((a) => a.appid)); },
    }),
    Save: async () => { self.saves += 1; },
  };
  return self;
}

let collections: FakeCollection[];
let created: string[];

beforeEach(() => {
  collections = [];
  created = [];
  (globalThis as Record<string, unknown>).collectionStore = {
    userCollections: collections,
    NewUnsavedCollection: (name: string) => {
      created.push(name);
      const made = collection(name);
      collections.push(made);
      return made;
    },
  };
  // Every app id used here is one Steam knows about.
  (globalThis as Record<string, unknown>).appStore = {
    GetAppOverviewByAppID: (appid: number) => ({ appid }),
  };
});

const LOWER = "shelfcloud (Shelfcloud)";
const PROPER = "Shelfcloud (Shelfcloud)";
const TILE = 2489416373;

describe("filing into a collection spelled differently", () => {
  it("adopts the one already there instead of minting a second id", async () => {
    collections.push(collection(LOWER));

    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);

    expect(created).toEqual([]);
    expect(collections).toHaveLength(1);
    expect(collections[0].added).toEqual([TILE]);
  });

  it("keeps the id, which is what the membership record hangs off", async () => {
    const existing = collection(LOWER);
    collections.push(existing);

    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);

    expect(collections[0].id).toBe(existing.id);
  });

  it("renames it to the spelling we use now", async () => {
    collections.push(collection(LOWER));

    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);

    expect(collections[0].displayName).toBe(PROPER);
  });

  it("writes the rename even when there is nothing to add", async () => {
    // else the rename is never saved and the old name comes back next sync.
    const existing = collection(LOWER, [TILE]);
    collections.push(existing);

    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);

    expect(existing.added).toEqual([]);
    expect(existing.displayName).toBe(PROPER);
    expect(existing.saves).toBe(1);
  });

  it("does not save when the name was already right and nothing is new", async () => {
    // Save makes Steam re-read the whole library; doing it for nothing was slow.
    const existing = collection(PROPER, [TILE]);
    collections.push(existing);

    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);

    expect(existing.saves).toBe(0);
  });

  it("still creates one when there is genuinely none", async () => {
    await addToProviderCollections([{ appId: TILE, provider: "shelfcloud" }]);
    expect(created).toEqual([PROPER]);
  });
});

describe("retiring", () => {
  it("does not delete a collection that differs only in case", async () => {
    const existing = collection(LOWER, [TILE]);
    collections.push(existing);

    await removeRetiredCollections(new Set([TILE]));

    expect(existing.deleted).toBe(false);
  });

  it("still deletes one named after a label we really dropped", async () => {
    const retired = collection("custom (Shelfcloud)", [TILE]);
    collections.push(retired);

    await removeRetiredCollections(new Set([TILE]));

    expect(retired.deleted).toBe(true);
  });
});

describe("pruning", () => {
  it("does not empty a collection spelled differently", async () => {
    const existing = collection(LOWER, [TILE]);
    collections.push(existing);

    await pruneProviderCollections(
      [{ appId: TILE, provider: "shelfcloud" }], new Set([TILE]));

    expect(existing.removedApps).toEqual([]);
  });

  it("still takes out a tile that moved to another service", async () => {
    const luna = collection("Amazon Luna (Shelfcloud)", [TILE]);
    collections.push(luna);

    // The tile launches through Boosteroid now, so it does not belong here.
    await pruneProviderCollections(
      [{ appId: TILE, provider: "boosteroid" }], new Set([TILE]));

    expect(luna.removedApps).toEqual([TILE]);
  });
});
