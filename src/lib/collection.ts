// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Sorting synced games into a Steam collection per cloud provider.
//
// One collection per provider rather than a single "Shelfcloud" bucket: a game
// can be on several providers, and a single mixed collection tells the user
// nothing about where anything actually runs. "Boosteroid" and "GeForce NOW"
// are meaningful groupings; "Shelfcloud" is just a pile.

import { providerLabel, WEBSITE_PROVIDERS } from "./types";

/** Steam's collection store, which @decky/ui does not type. */
interface SteamCollection {
  displayName: string;
  id: string;
  allApps?: Array<{ appid: number }>;
  AsDragDropCollection(): { AddApps(apps: unknown[]): void };
  /** Takes the same overview objects AddApps does. Probed on hardware:
   *  SetApps stores the array itself and builds a Set from it, so these
   *  are compared by identity — pass back what allApps already holds. */
  RemoveApps(apps: unknown[]): void;
  Save(): Promise<unknown>;
}

interface CollectionStore {
  userCollections: SteamCollection[];
  /**
   * Signature is (name, filter, apps) — all three required. Called with fewer
   * it throws "Cannot read properties of undefined (reading 'map')", because it
   * maps over the apps argument. Established by probing on hardware.
   */
  NewUnsavedCollection(name: string, filter: null, apps: unknown[]): SteamCollection;
}

declare const collectionStore: CollectionStore;
declare const appStore: { GetAppOverviewByAppID(appId: number): unknown };

export interface CollectionMember {
  appId: number;
  provider: string;
}

/**
 * Collection name for a provider, e.g. "Boosteroid (Shelfcloud)".
 *
 * Suffixed rather than named after the provider alone: a bare "Boosteroid"
 * could collide with a collection the user already keeps, and this makes it
 * obvious which collections Shelfcloud manages and may add to or empty.
 */
export function collectionName(provider: string): string {
  // Both website providers share one collection. A published page and a page
  // the user added are the same kind of thing to the person looking at their
  // library — a link — and splitting them produced two collections, one of
  // which was called "custom" because the label was missing entirely.
  const label = WEBSITE_PROVIDERS.has(provider) ? "Websites" : providerLabel(provider);
  return `${label} (Shelfcloud)`;
}

/**
 * Put each synced app into the collection for the provider it launches through.
 *
 * Best effort: failing to sort tiles leaves them playable, which is not worth
 * failing a sync over.
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
      if (!overviews.length) continue;

      const existing = collectionStore.userCollections.find((c) => c.displayName === name);
      if (existing) {
        // Save() rewrites the whole collection and makes Steam re-read its
        // library, so it is the expensive part of a sync - measured at roughly
        // half a second. Adding an app that is already filed still costs a
        // full Save, and a sync that changed NOTHING was paying ~33 of them
        // (388 games in batches of 12): fifteen to twenty seconds to do
        // nothing at all.
        //
        // allApps is the collection's own membership, so the ones already
        // there can be dropped and the write skipped entirely when none are
        // new. A game the user removed from the collection by hand is still
        // re-filed, because it is genuinely absent - only redundant writes go.
        const already = new Set(
          ((existing.allApps ?? []) as Array<{ appid: number }>).map((app) => app.appid));
        const missing = overviews.filter(
          (app) => !already.has((app as { appid: number }).appid));
        if (!missing.length) continue;

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
 * Take out of each provider collection the games that no longer belong in it.
 *
 * addToProviderCollections only ever ADDS, so a tile filed under one provider
 * stayed there after the provider it launches through changed — Fortnite sat
 * in "Amazon Luna (Shelfcloud)" and opened GeForce NOW, because the filing
 * happens at sync and the launch URL is resolved later by the gateway, which
 * reads the user's preferred service. Nothing ever disagreed; nothing ever
 * cleaned up either.
 *
 * Scoped twice over, because these collections are visible to the user and
 * emptying the wrong thing is worse than a misfiled tile:
 *
 *   - only collections named "… (Shelfcloud)", which the naming comment above
 *     says outright are ours to add to or empty;
 *   - only apps Shelfcloud manages. A game the user dropped into one of our
 *     collections by hand is left exactly where they put it.
 *
 * `members` must be the WHOLE sync, not a batch: the question asked of each
 * collection is "what should be in you", which a slice cannot answer.
 */
export async function pruneProviderCollections(
  members: CollectionMember[],
  managedAppIds: Set<number>,
): Promise<string[]> {
  if (typeof collectionStore === "undefined") return [];

  const wanted = new Map<string, Set<number>>();
  for (const member of members) {
    const name = collectionName(member.provider);
    const set = wanted.get(name) ?? new Set<number>();
    set.add(member.appId);
    wanted.set(name, set);
  }

  const pruned: string[] = [];
  for (const collection of collectionStore.userCollections) {
    if (!isOurs(collection.displayName)) continue;
    try {
      const belongs = wanted.get(collection.displayName) ?? new Set<number>();
      // From allApps, so the objects handed back are the ones Steam holds.
      const stale = (collection.allApps ?? []).filter(
        (app) => managedAppIds.has(app.appid) && !belongs.has(app.appid));
      if (!stale.length) continue;

      collection.RemoveApps(stale);
      await collection.Save();
      pruned.push(collection.displayName);
    } catch (error) {
      // Best effort, like the filing itself: a tile in the wrong collection
      // still plays, and failing a sync over tidiness helps nobody.
      console.error("[shelfcloud] could not prune collection",
                    collection.displayName, error);
    }
  }
  return pruned;
}
