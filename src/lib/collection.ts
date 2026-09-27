// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Sorts synced games into one Steam collection per cloud provider. One per
// provider, not a single mixed bucket, since a game can be on several and a
// mixed pile tells nothing about where it runs.

import { KNOWN_PROVIDERS, providerLabel } from "./types";

/** Steam's collection store, which @decky/ui does not type. */
interface SteamCollection {
  displayName: string;
  id: string;
  allApps?: Array<{ appid: number }>;
  AsDragDropCollection(): { AddApps(apps: unknown[]): void };
  /** Compared by identity, so pass back what allApps already holds. */
  RemoveApps(apps: unknown[]): void;
  Save(): Promise<unknown>;
  /** Also drops the cloud entry, which is what makes a delete stick -
   *  collections live in Steam Cloud, a local-only remove comes back on sync.
   *  Optional because an older client may not have it. */
  Delete?(): Promise<unknown>;
}

interface CollectionStore {
  userCollections: SteamCollection[];
  /** All three args required - it maps over apps and throws otherwise. */
  NewUnsavedCollection(name: string, filter: null, apps: unknown[]): SteamCollection;
}

declare const collectionStore: CollectionStore;
declare const appStore: { GetAppOverviewByAppID(appId: number): unknown };

export interface CollectionMember {
  appId: number;
  provider: string;
}

/**
 * Collection name for a provider, e.g. "Boosteroid (Shelfcloud)". Suffixed so
 * it cannot collide with a collection the user keeps, and to mark which ones
 * we manage.
 */
export function collectionName(provider: string): string {
  // One per provider, web pages included. Merging them into a single
  // "Websites" collection mixed our published page with the user's own and
  // left a provider collection empty.
  return `${providerLabel(provider)} (Shelfcloud)`;
}

/**
 * Our collection under this name, whatever case it is spelled in right now.
 *
 * Case-insensitive on purpose: an old "shelfcloud (Shelfcloud)" record kept
 * coming back from the cloud, an exact-match lookup did not see it as ours,
 * and every sync minted a second collection under the proper case. The tile
 * flickered in and out. Adopting the existing one stops that cycle. For
 * shortcuts the id IS the membership - it is local, not cloud.
 */
function findCollection(name: string): SteamCollection | undefined {
  const wanted = name.toLowerCase();
  return collectionStore.userCollections.find(
    (c) => c.displayName?.toLowerCase() === wanted);
}

/**
 * File each synced app into the collection for its provider. Best effort - a
 * misfiled tile still plays, no sync should fail over it.
 */
export async function addToProviderCollections(members: CollectionMember[]): Promise<string[]> {
  if (!members.length) return [];
  if (typeof collectionStore === "undefined") return [];

  const byProvider = new Map<string, number[]>();
  for (const member of members) {
    const name = collectionName(member.provider);
    const list = byProvider.get(name) ?? [];
    list.push(member.appId);
    byProvider.set(name, list);
  }

  const updated: string[] = [];
  for (const [name, appIds] of byProvider) {
    try {
      const overviews = appIds
        .map((id) => appStore.GetAppOverviewByAppID(id))
        .filter(Boolean);
      if (!overviews.length) {
        // Logged, not skipped silently: this continue is the only path that
        // leaves a collection empty without recording why.
        console.warn("[shelfcloud] not filing", name,
                     "— appStore knows none of", appIds.join(", "));
        continue;
      }
      const unknown = appIds.length - overviews.length;
      if (unknown > 0) {
        console.warn("[shelfcloud] filing", name, "without", unknown,
                     "tile(s) appStore does not know");
      }

      const existing = findCollection(name);
      if (existing) {
        // Adopt it under the current spelling instead of making a second one
        // beside it - see findCollection. Assigning displayName because there
        // is no SetName; it is an acessor with a setter behind it.
        let renamed = false;
        if (existing.displayName !== name) {
          try {
            existing.displayName = name;
            renamed = true;
          } catch { /* the id is the membership; the spelling is cosmetic */ }
        }
        // Save() is the expensive part of a sync (~half a second), so skip the
        // write when nothing is new. allApps is the current membership; a game
        // the user removed by hand is genuinely absent and still re-filed.
        const already = new Set(
          ((existing.allApps ?? []) as Array<{ appid: number }>).map((app) => app.appid));
        const missing = overviews.filter(
          (app) => !already.has((app as { appid: number }).appid));
        if (!missing.length) {
          // Nothing to add, but a rename still has to be written or the name
          // reverts and the collection is a stranger again on the next sync.
          if (renamed) await existing.Save();
          continue;
        }

        existing.AsDragDropCollection().AddApps(missing);
        await existing.Save();
      } else {
        const created = collectionStore.NewUnsavedCollection(name, null, overviews);
        await created.Save();
      }
      updated.push(name);
    } catch (error) {
      console.error("[shelfcloud] could not update collection", name, error);
    }
  }
  return updated;
}

/** Does this collection belong to Shelfcloud? */
function isOurs(displayName: string): boolean {
  return displayName.endsWith(" (Shelfcloud)");
}

/**
 * Delete collections we made under a label we no longer use, e.g. a provider
 * whose label changed leaves an emptied collection behind forever. Steam's own
 * DeleteCollection drops the cloud entry too, otherwise the next sync pulls it
 * back.
 *
 * Scoped tight, since this deletes: only "… (Shelfcloud)" names, only ones no
 * current provider uses, and only when everything inside is a tile we manage -
 * a game the user dragged in by hand is left alone. Runs after filing and
 * pruning so the games are already in their new home.
 */
export async function removeRetiredCollections(
  managedAppIds: Set<number>,
): Promise<string[]> {
  if (typeof collectionStore === "undefined") return [];

  // Lower-cased so a collection is judged by what it is, not its current
  // spelling - see the flicker cycle on findCollection.
  const current = new Set(KNOWN_PROVIDERS.map((p) => collectionName(p).toLowerCase()));
  const removed: string[] = [];
  // Over a copy: deleting mutates userCollections underneath us.
  for (const collection of [...collectionStore.userCollections]) {
    const name = collection.displayName;
    if (!isOurs(name) || current.has(name.toLowerCase())) continue;
    if (typeof collection.Delete !== "function") continue;
    const strangers = (collection.allApps ?? []).filter(
      (app) => !managedAppIds.has(app.appid));
    if (strangers.length) {
      console.warn("[shelfcloud] leaving", name,
                   "— it holds games this plugin does not manage");
      continue;
    }
    try {
      await collection.Delete();
      removed.push(name);
    } catch (error) {
      // Best effort - an extra collection is untidy, not broken.
      console.error("[shelfcloud] could not delete collection", name, error);
    }
  }
  return removed;
}

/**
 * Remove from each provider collection the games that no longer belong. Filing
 * only ever adds, so a tile stayed put after his provider changed. Scoped to
 * "… (Shelfcloud)" names and apps we manage, since emptying the wrong thing is
 * worse than a misfiled tile. `members` must be the WHOLE sync, not a batch -
 * a slice cannot answer "what should be in you".
 */
export async function pruneProviderCollections(
  members: CollectionMember[],
  managedAppIds: Set<number>,
): Promise<string[]> {
  if (typeof collectionStore === "undefined") return [];

  // Keyed and read lower-case: an older spelling matched nothing here, so
  // belongs came out empty and every managed tile got pruned away.
  const wanted = new Map<string, Set<number>>();
  for (const member of members) {
    const name = collectionName(member.provider).toLowerCase();
    const set = wanted.get(name) ?? new Set<number>();
    set.add(member.appId);
    wanted.set(name, set);
  }

  const pruned: string[] = [];
  for (const collection of collectionStore.userCollections) {
    if (!isOurs(collection.displayName)) continue;
    try {
      const belongs = wanted.get(collection.displayName.toLowerCase()) ?? new Set<number>();
      // From allApps, so the objects handed back are the ones Steam holds.
      const stale = (collection.allApps ?? []).filter(
        (app) => managedAppIds.has(app.appid) && !belongs.has(app.appid));
      if (!stale.length) continue;

      collection.RemoveApps(stale);
      await collection.Save();
      pruned.push(collection.displayName);
    } catch (error) {
      // Best effort - a tile in the wrong collection still plays.
      console.error("[shelfcloud] could not prune collection",
                    collection.displayName, error);
    }
  }
  return pruned;
}
