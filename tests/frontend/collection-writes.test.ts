// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Saving a collection is the expensive part of a sync: Save() rewrites the
// whole collection and makes Steam re-read its library, ~half a second, even
// when the app is already filed. The loop pushed every tile it walked past, so
// a no-op sync of 388 games still paid ~33 Saves and "Sync now" hung for
// fifteen to twenty seconds doing nothing.

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
  // the sync passes app ids; the store turns them into overviews.
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
    // genuinely absent, so not a redundant write. only no-op writes are skipped.
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
    // the bug: 388 tiles all already filed, pushed in batches of 12, 33 saves.
    const ids = Array.from({ length: 388 }, (_, i) => i + 1);
    collections.push(collection(BOOSTEROID, ids));

    for (let i = 0; i < ids.length; i += 12) {
      await addToProviderCollections(
        ids.slice(i, i + 12).map((appId) => ({ appId, provider: "boosteroid" })));
    }

    expect(collections[0].saves).toBe(0);
  });
});
