// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Saving a collection is the expensive part of a sync.
//
// Save() rewrites the whole collection and makes Steam re-read its library —
// measured at roughly half a second. Adding an app that is ALREADY filed still
// costs a full Save, and the sync loop pushed every tile it walked past, not
// only the ones it changed.
//
// So a sync that changed nothing still paid one Save per batch of 12. With 388
// games that is ~33 of them: pressing "Sync now" took fifteen to twenty seconds
// to do nothing at all, which is what sent someone looking.
//
// Measured on a real Deck, mid-sync:
//     Boosteroid (Shelfcloud)   301 apps
//     GeForce NOW (Shelfcloud)   84 apps
//     Amazon Luna (Shelfcloud)    2 apps
// — already complete, and re-saved on every sync regardless.

import { beforeEach, describe, expect, it } from "vitest";

import { addToProviderCollections } from "../../src/lib/collection";

type FakeCollection = {
  displayName: string;
  allApps: Array<{ appid: number }>;
  added: number[][];
  saves: number;
  AsDragDropCollection: () => { AddApps: (apps: Array<{ appid: number }>) => void };
  Save: () => Promise<void>;
};

function collection(name: string, appIds: number[]): FakeCollection {
  const self: FakeCollection = {
    displayName: name,
    allApps: appIds.map((appid) => ({ appid })),
    added: [],
    saves: 0,
    AsDragDropCollection: () => ({
      AddApps: (apps) => { self.added.push(apps.map((a) => a.appid)); },
    }),
    Save: async () => { self.saves += 1; },
  };
  return self;
}

let collections: FakeCollection[];
let created: Array<{ name: string; appIds: number[]; saves: number }>;

beforeEach(() => {
  collections = [];
  created = [];
  (globalThis as Record<string, unknown>).collectionStore = {
    userCollections: collections,
    NewUnsavedCollection: (name: string, _: unknown, overviews: Array<{ appid: number }>) => {
      const record = { name, appIds: overviews.map((o) => o.appid), saves: 0 };
      created.push(record);
      return { Save: async () => { record.saves += 1; } };
    },
  };
  // The sync passes app ids; the store turns them into overviews.
  (globalThis as Record<string, unknown>).appStore = {
    GetAppOverviewByAppID: (appid: number) => ({ appid }),
  };
});

const BOOSTEROID = "Boosteroid (Shelfcloud)";

describe("a collection is only written when something is actually new", () => {
  it("does not save when every app is already filed", async () => {
    collections.push(collection(BOOSTEROID, [1, 2, 3]));

    await addToProviderCollections([
      { appId: 1, provider: "boosteroid" },
      { appId: 2, provider: "boosteroid" },
      { appId: 3, provider: "boosteroid" },
    ]);

    expect(collections[0].saves).toBe(0);
    expect(collections[0].added).toEqual([]);
  });

  it("saves once when some are new, and adds only those", async () => {
    collections.push(collection(BOOSTEROID, [1, 2]));

    await addToProviderCollections([
      { appId: 1, provider: "boosteroid" },   // already there
      { appId: 7, provider: "boosteroid" },   // new
    ]);

    expect(collections[0].saves).toBe(1);
    expect(collections[0].added).toEqual([[7]]);
  });

  it("still re-files a game the user removed by hand", async () => {
    // It is genuinely absent, so this is not a redundant write. Only writes
    // that would change nothing are skipped.
    collections.push(collection(BOOSTEROID, [1, 2]));

    await addToProviderCollections([{ appId: 3, provider: "boosteroid" }]);

    expect(collections[0].added).toEqual([[3]]);
    expect(collections[0].saves).toBe(1);
  });

  it("creates the collection when there is none", async () => {
    await addToProviderCollections([
      { appId: 4, provider: "boosteroid" },
      { appId: 5, provider: "boosteroid" },
    ]);

    expect(created).toHaveLength(1);
    expect(created[0].name).toBe(BOOSTEROID);
    expect(created[0].appIds).toEqual([4, 5]);
    expect(created[0].saves).toBe(1);
  });

  it("writes each provider at most once", async () => {
    collections.push(collection(BOOSTEROID, [1]));
    collections.push(collection("GeForce NOW (Shelfcloud)", []));

    await addToProviderCollections([
      { appId: 1, provider: "boosteroid" },     // already filed
      { appId: 2, provider: "geforce_now" },
      { appId: 3, provider: "geforce_now" },
    ]);

    expect(collections[0].saves).toBe(0);       // nothing new for Boosteroid
    expect(collections[1].saves).toBe(1);       // one write for both GFN games
    expect(collections[1].added).toEqual([[2, 3]]);
  });

  it("a whole no-op sync writes nothing at all", async () => {
    // The shape of the bug: 388 tiles, every one already filed, pushed through
    // in batches of 12. Thirty-three saves, nothing changed.
    const ids = Array.from({ length: 388 }, (_, i) => i + 1);
    collections.push(collection(BOOSTEROID, ids));

    for (let i = 0; i < ids.length; i += 12) {
      await addToProviderCollections(
        ids.slice(i, i + 12).map((appId) => ({ appId, provider: "boosteroid" })));
    }

    expect(collections[0].saves).toBe(0);
  });
});
