// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Taking games OUT of a provider collection when they no longer belong there.
//
// Filing only ever added, so a tile stayed in the collection it was first put
// in. Fortnite is genuinely on Luna, Boosteroid and GeForce NOW; it was filed
// under Luna and then launched GeForce NOW, because the collection is chosen
// at sync time and the launch URL is resolved later by the gateway, which
// reads the user's preferred service. Neither half was wrong — nothing ever
// reconciled them.
//
// These collections are visible in the user's own library, so the two limits
// below matter more than the removal itself: only collections named
// "… (Shelfcloud)", and only apps Shelfcloud manages.

import { beforeEach, describe, expect, it } from "vitest";

import { pruneProviderCollections } from "../../src/lib/collection";

type FakeCollection = {
  displayName: string;
  allApps: Array<{ appid: number }>;
  removed: number[][];
  saves: number;
  RemoveApps: (apps: Array<{ appid: number }>) => void;
  AsDragDropCollection: () => { AddApps: (apps: unknown[]) => void };
  Save: () => Promise<void>;
};

function collection(name: string, appIds: number[]): FakeCollection {
  const self: FakeCollection = {
    displayName: name,
    allApps: appIds.map((appid) => ({ appid })),
    removed: [],
    saves: 0,
    RemoveApps: (apps) => { self.removed.push(apps.map((a) => a.appid)); },
    AsDragDropCollection: () => ({ AddApps: () => {} }),
    Save: async () => { self.saves += 1; },
  };
  return self;
}

let collections: FakeCollection[];

beforeEach(() => {
  collections = [];
  (globalThis as Record<string, unknown>).collectionStore = {
    userCollections: collections,
    NewUnsavedCollection: () => ({ Save: async () => {} }),
  };
});

const LUNA = "Amazon Luna (Shelfcloud)";
const GFN = "GeForce NOW (Shelfcloud)";

describe("a game that moved provider leaves the old collection", () => {
  it("removes it from the collection it no longer launches through", async () => {
    collections.push(collection(LUNA, [42]));
    collections.push(collection(GFN, [42]));

    // Fortnite now launches GeForce NOW.
    await pruneProviderCollections([{ appId: 42, provider: "geforce_now" }],
                                   new Set([42]));

    expect(collections[0].removed).toEqual([[42]]);
    expect(collections[0].saves).toBe(1);
    // And is left alone where it does belong.
    expect(collections[1].removed).toEqual([]);
    expect(collections[1].saves).toBe(0);
  });

  it("removes a game that left the library altogether", async () => {
    collections.push(collection(LUNA, [42]));

    // Filed nowhere this sync, but Shelfcloud tracked it before — the shortcut
    // has just been deleted, and without this it sits in the collection for
    // good.
    await pruneProviderCollections([], new Set([42]));

    expect(collections[0].removed).toEqual([[42]]);
  });

  it("writes nothing when every collection is already right", async () => {
    collections.push(collection(GFN, [1, 2]));

    await pruneProviderCollections(
      [{ appId: 1, provider: "geforce_now" }, { appId: 2, provider: "geforce_now" }],
      new Set([1, 2]));

    expect(collections[0].saves).toBe(0);
    expect(collections[0].removed).toEqual([]);
  });
});

describe("what it must not touch", () => {
  it("leaves a game the user filed there by hand", async () => {
    // 99 is not Shelfcloud's: somebody dragged their own game in here.
    collections.push(collection(LUNA, [42, 99]));

    await pruneProviderCollections([{ appId: 42, provider: "geforce_now" }],
                                   new Set([42]));

    expect(collections[0].removed).toEqual([[42]]);
    expect(collections[0].removed.flat()).not.toContain(99);
  });

  it("leaves collections that are not ours", async () => {
    // A bare provider name is the user's own collection, which is exactly why
    // ours carry the suffix.
    collections.push(collection("Amazon Luna", [42]));
    collections.push(collection("Favourites", [42]));

    await pruneProviderCollections([{ appId: 42, provider: "geforce_now" }],
                                   new Set([42]));

    expect(collections[0].removed).toEqual([]);
    expect(collections[1].removed).toEqual([]);
    expect(collections[0].saves + collections[1].saves).toBe(0);
  });

  it("removes nothing when the sync filed nothing and tracked nothing", async () => {
    // An empty managed set is the "we know of no apps" case — a failed sync
    // must not read as "every collection should be emptied".
    collections.push(collection(LUNA, [42, 43]));

    await pruneProviderCollections([], new Set());

    expect(collections[0].removed).toEqual([]);
    expect(collections[0].saves).toBe(0);
  });
});

describe("it survives a collection that misbehaves", () => {
  it("keeps going when one collection throws", async () => {
    const broken = collection(LUNA, [42]);
    broken.RemoveApps = () => { throw new Error("nope"); };
    collections.push(broken);
    collections.push(collection(GFN, [42]));

    // Filed under neither, so both would be pruned; the first throws.
    await expect(
      pruneProviderCollections([{ appId: 42, provider: "boosteroid" }], new Set([42])),
    ).resolves.toBeDefined();

    expect(collections[1].removed).toEqual([[42]]);
  });
});
