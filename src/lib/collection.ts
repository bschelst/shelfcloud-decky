// Sorting synced games into a Steam collection per cloud provider.
//
// One collection per provider rather than a single "Shelfcloud" bucket: a game
// can be on several providers, and a single mixed collection tells the user
// nothing about where anything actually runs. "Boosteroid" and "GeForce NOW"
// are meaningful groupings; "Shelfcloud" is just a pile.

import { providerLabel } from "./types";

/** Steam's collection store, which @decky/ui does not type. */
interface SteamCollection {
  displayName: string;
  id: string;
  allApps?: Array<{ appid: number }>;
  AsDragDropCollection(): { AddApps(apps: unknown[]): void };
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
    const name = providerLabel(member.provider);
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
        existing.AsDragDropCollection().AddApps(overviews);
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
