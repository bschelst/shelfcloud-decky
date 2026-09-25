// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A collection that vanished and came back every few minutes.
//
// Observed on hardware, not reasoned about: the "Shelfcloud (Shelfcloud)"
// collection took turns with a "shelfcloud (Shelfcloud)" one — same name, one
// capital letter apart — and got a BRAND NEW id every time it came back. Seven
// ids in twelve minutes. Steam's config had 941 shortcut-membership records
// for thirteen collections; the rest were orphans, one per cycle.
//
// The id is the part that matters. A collection's membership for SHORTCUTS is
// stored locally, keyed by collection id, because shortcut app ids are
// generated per machine and cannot roam — the cloud record for one of ours is
// `"added":[]` by design. Mint a new id and the membership is left behind.
//
// Three places compared the name exactly, and each one broke on the mismatch:
//
//   - filing did not recognise the collection as ours, so it made a second
//     one under the proper spelling, with a new id;
//   - retiring saw a name that is not in the current set and deleted it;
//   - pruning found nothing "wanted" under that name and stripped every
//     managed tile out of it.
//
// The first two took turns forever. The third emptied it with no help at all.

import { beforeEach, describe, expect, it } from "vitest";

import {
  addToProviderCollections,
  pruneProviderCollections,
  removeRetiredCollections,
} from "../../src/lib/collection";

type FakeCollection = {
  id: string;
  displayName: string;
  allApps: Array<{ appid: number }>;
  deleted: boolean;
  added: number[];
  removedApps: number[];
  Delete: () => Promise<void>;
  SetName: (name: string) => void;
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
    Delete: async () => { self.deleted = true; },
    SetName: (next: string) => { self.displayName = next; },
    RemoveApps: (apps) => { self.removedApps.push(...apps.map((a) => a.appid)); },
    AsDragDropCollection: () => ({
      AddApps: (apps) => { self.added.push(...apps.map((a) => a.appid)); },
    }),
    Save: async () => {},
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
