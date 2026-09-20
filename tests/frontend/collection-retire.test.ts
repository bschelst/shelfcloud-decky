// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Deleting collections this plugin created under a name it no longer uses.
//
// Nothing ever deleted a collection. Filing created them, pruning emptied
// them, and that was the whole lifecycle — so when a provider's LABEL changed,
// the collection under the old name stayed in the user's library forever.
//
// "custom (Shelfcloud)" is the one that prompted this. Website tiles were
// filed under the raw provider key before `custom` was given the label
// "Websites", and the renaming fixed what NEW collections are called without
// touching the one already sitting there. Worse, deleting it by hand did not
// help: collections live in Steam Cloud, so the next sync pulled it back.
// Steam's own DeleteCollection drops the cloud entry as well, which is what
// makes the removal stick.

import { beforeEach, describe, expect, it } from "vitest";

import { collectionName, removeRetiredCollections } from "../../src/lib/collection";
import { KNOWN_PROVIDERS } from "../../src/lib/types";

type FakeCollection = {
  id: string;
  displayName: string;
  allApps: Array<{ appid: number }>;
  deleted: boolean;
  Delete: () => Promise<void>;
  RemoveApps: (apps: Array<{ appid: number }>) => void;
  AsDragDropCollection: () => { AddApps: (apps: unknown[]) => void };
  Save: () => Promise<void>;
};

function collection(name: string, appIds: number[] = []): FakeCollection {
  const self: FakeCollection = {
    id: `id-${name}`,
    displayName: name,
    allApps: appIds.map((appid) => ({ appid })),
    deleted: false,
    Delete: async () => { self.deleted = true; },
    RemoveApps: () => {},
    AsDragDropCollection: () => ({ AddApps: () => {} }),
    Save: async () => {},
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

const RETIRED = "custom (Shelfcloud)";
const CURRENT = "Websites (Shelfcloud)";

describe("a collection named after a label we no longer use", () => {
  it("is deleted", async () => {
    const stale = collection(RETIRED);
    collections.push(stale);
    const gone = await removeRetiredCollections(new Set());
    expect(stale.deleted).toBe(true);
    expect(gone).toEqual([RETIRED]);
  });

  it("is deleted even when it still holds the tiles we manage", async () => {
    // The games are not lost — the same sync has already filed them under the
    // current name. Only the grouping goes.
    const stale = collection(RETIRED, [11, 22]);
    collections.push(stale);
    collections.push(collection(CURRENT, [11, 22]));
    await removeRetiredCollections(new Set([11, 22]));
    expect(stale.deleted).toBe(true);
  });
});

describe("what it must not touch", () => {
  it("leaves every current provider collection alone", async () => {
    for (const provider of KNOWN_PROVIDERS) {
      collections.push(collection(collectionName(provider), [1]));
    }
    const gone = await removeRetiredCollections(new Set([1]));
    expect(gone).toEqual([]);
    expect(collections.every((c) => !c.deleted)).toBe(true);
  });

  it("leaves collections that are not ours alone", async () => {
    // No " (Shelfcloud)" suffix, so not ours to delete however odd the name.
    for (const name of ["custom", "Websites", "Favourites", "custom (Shelfcloud) "]) {
      collections.push(collection(name, [1]));
    }
    expect(await removeRetiredCollections(new Set([1]))).toEqual([]);
    expect(collections.every((c) => !c.deleted)).toBe(true);
  });

  it("keeps a retired collection that holds a game we do not manage", async () => {
    // Somebody dragged their own game in. Deleting the collection would throw
    // away a grouping they made by hand, which is not ours to do — the games
    // we manage have already been filed correctly elsewhere.
    const stale = collection(RETIRED, [11, 999]);
    collections.push(stale);
    const gone = await removeRetiredCollections(new Set([11]));
    expect(stale.deleted).toBe(false);
    expect(gone).toEqual([]);
  });
});

describe("failure is not fatal", () => {
  it("carries on when one delete throws", async () => {
    const angry = collection(RETIRED);
    angry.Delete = async () => { throw new Error("nope"); };
    const other = collection("nonsense (Shelfcloud)");
    collections.push(angry, other);
    const gone = await removeRetiredCollections(new Set());
    expect(gone).toEqual(["nonsense (Shelfcloud)"]);
    expect(other.deleted).toBe(true);
  });

  it("does nothing at all when there is no collection store", async () => {
    delete (globalThis as Record<string, unknown>).collectionStore;
    expect(await removeRetiredCollections(new Set())).toEqual([]);
  });

  it("skips a collection too old to have Delete", async () => {
    const stale = collection(RETIRED);
    (stale as unknown as { Delete?: unknown }).Delete = undefined;
    collections.push(stale);
    expect(await removeRetiredCollections(new Set())).toEqual([]);
  });
});
