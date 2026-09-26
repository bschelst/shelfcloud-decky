// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What the library badge needs to know, fetched once and shared.
//
// The badge mounts every time a game page is opened — which on a Deck is
// constantly, and on a library of several hundred cloud tiles is the most
// frequently mounted component this plugin has. Fetching per mount would put
// two RPCs behind every press of the A button.
//
// So both answers live at module scope with a short life, and concurrent
// callers share one in-flight request rather than starting their own. Neither
// answer changes except when a sync runs or a setting is changed, and both of
// those already tell us.

import { useEffect, useState } from "react";

import { getStatus, trackedProviders } from "./api";

/** How long an answer may be reused. Long enough that paging through a
 *  library costs one request, short enough that a sync finishing elsewhere
 *  shows up without anyone reopening the panel. */
const CACHE_MS = 30_000;

interface Cached<T> {
  at: number;
  value: T;
  inFlight: Promise<T> | null;
}

function emptyCache<T>(value: T): Cached<T> {
  return { at: 0, value, inFlight: null };
}

let providers = emptyCache<Record<string, string>>({});
let badge = emptyCache<{ enabled: boolean; position: string }>(
  { enabled: true, position: "bl" });

/** Throw both answers away, so the next badge asks again.
 *
 *  Called when a sync finishes: tiles have been made or removed, so the
 *  app-id map is stale by definition. */
export function invalidateBadgeData(): void {
  providers = emptyCache(providers.value);
  badge = emptyCache(badge.value);
}

async function read<T>(cache: Cached<T>, fetcher: () => Promise<T>): Promise<T> {
  const now = Date.now();
  if (cache.at && now - cache.at < CACHE_MS) return cache.value;
  // One request for however many callers arrive while it is open. Without
  // this, opening a page mid-sync could start several at once.
  if (!cache.inFlight) {
    cache.inFlight = fetcher()
      .then((value) => {
        cache.value = value;
        cache.at = Date.now();
        return value;
      })
      .catch(() => cache.value)        // keep the last good answer
      .finally(() => { cache.inFlight = null; });
  }
  return cache.inFlight;
}

/** Which service a tile launches, or undefined when it is not one of ours.
 *
 *  Undefined is the normal answer: most games on a Deck are not cloud tiles,
 *  and the badge renders nothing for them. That is the whole meaning of it. */
export function useProviderForApp(appId: string | undefined): string | undefined {
  const [provider, setProvider] = useState<string | undefined>(
    appId ? providers.value[appId] : undefined);

  useEffect(() => {
    let cancelled = false;
    if (!appId) { setProvider(undefined); return undefined; }

    void read(providers, async () => {
      const result = await trackedProviders();
      return result?.ok && result.providers ? result.providers : {};
    }).then((map) => {
      if (!cancelled) setProvider(map[appId]);
    });

    return () => { cancelled = true; };
  }, [appId]);

  return provider;
}

/** Whether to show the badge, and where. */
export function useBadgeSettings(): { enabled: boolean; position: string } {
  const [value, setValue] = useState(badge.value);

  useEffect(() => {
    let cancelled = false;
    void read(badge, async () => {
      const status = await getStatus();
      return {
        enabled: status?.badge_enabled !== false,
        position: status?.badge_position ?? "bl",
      };
    }).then((next) => { if (!cancelled) setValue(next); });
    return () => { cancelled = true; };
  }, []);

  return value;
}
