// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What the library badge needs to know, fetched once and shared. The badge
// mounts every time a game page opens, which on a Deck is constantly, so
// fetching per mount would put two RPCs behind every A press. Both answers live
// at module scope with a short life, and callers arriving together share one
// request. Neither changes except on a sync or a setting change.

import { useEffect, useState } from "react";

import { currentPlayers, getStatus, trackedProviders } from "./api";

/** How long an answer may be reused. Long enough that paging a library costs
 *  one request, short enough that a sync finishing elsewhere shows up. */
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
/** app id -> the game's id on STEAM, for tiles whose game is on Steam. */
let steamIds = emptyCache<Record<string, string>>({});
let badge = emptyCache<{
  enabled: boolean; onCards: boolean; position: string;
  players: boolean; playersPosition: string;
}>({ enabled: true, onCards: true, position: "bl",
     players: false, playersPosition: "tr" });

/** Throw both answers away so the next badge asks again. Called when a sync
 *  finishes: tiles were made or removed, so the app-id map is stale. */
export function invalidateBadgeData(): void {
  providers = emptyCache(providers.value);
  steamIds = emptyCache(steamIds.value);
  badge = emptyCache(badge.value);
}

/** The last answers, without waiting. For the focused-card badge in a mutation
 *  observer, which must draw right away — an await would draw a frame late.
 *  Starts as "nothing is ours", so a card before the first fetch gets no badge. */
export function badgeDataNow(): {
  providers: Record<string, string>; enabled: boolean; position: string;
} {
  return {
    providers: providers.value,
    // The card badge has its own switch. Turning the game-page one off should
    // not take this with it, they are two places and someone may want just one.
    enabled: badge.value.onCards,
    position: badge.value.position,
  };
}

/** Fetch both answers if they are stale, for a caller that cannot await. */
export function refreshBadgeData(): void {
  void read(providers, async () => {
    const result = await trackedProviders();
    return result?.ok && result.providers ? result.providers : {};
  });
  void read(steamIds, async () => {
    const result = await trackedProviders();
    return result?.ok && result.steam_ids ? result.steam_ids : {};
  });
  void read(badge, async () => {
    const status = await getStatus();
    return {
      enabled: status?.badge_enabled !== false,
      onCards: status?.badge_on_cards !== false,
      position: status?.badge_position ?? "bl",
      // off by default. it asks Steam a question on every page open.
      players: status?.players_enabled === true,
      playersPosition: status?.players_position ?? "tr",
    };
  });
}

async function read<T>(cache: Cached<T>, fetcher: () => Promise<T>): Promise<T> {
  const now = Date.now();
  if (cache.at && now - cache.at < CACHE_MS) return cache.value;
  // one request for all the callers that arrive while it is open. without this,
  // opening a page mid-sync could start several at once.
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
 *  Undefined is the normal answer, most games are not cloud tiles. */
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
export function useBadgeSettings(): {
  enabled: boolean; onCards: boolean; position: string;
  players: boolean; playersPosition: string;
  /** False until the real settings are read. A badge drawn on the guessed
   *  defaults shows up then vanishes, so nothing draws until this is true. */
  loaded: boolean;
} {
  const [value, setValue] = useState(badge.value);
  // `at` is 0 until a fetch has landed. module scope, so a second page open in
  // the same session already knows and does not flash.
  const [loaded, setLoaded] = useState(badge.at !== 0);

  useEffect(() => {
    let cancelled = false;
    void read(badge, async () => {
      const status = await getStatus();
      return {
        enabled: status?.badge_enabled !== false,
        onCards: status?.badge_on_cards !== false,
        position: status?.badge_position ?? "bl",
        players: status?.players_enabled === true,
        // top right by default, so it does not land on the provider badge,
        // which defaults to bottom left.
        playersPosition: status?.players_position ?? "tr",
      };
    }).then((next) => {
      if (cancelled) return;
      setValue(next);
      setLoaded(true);
    });
    return () => { cancelled = true; };
  }, []);

  return { ...value, loaded };
}


/** How many people play this tile's game on Steam, if anyone can say. Undefined
 *  covers three kinds of "no": not ours, not on Steam, or Steam had no answer —
 *  none is a zero. Not cached here, current_players caches on the backend. */
export function usePlayersForApp(appId: string | undefined): number | undefined {
  const [players, setPlayers] = useState<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setPlayers(undefined);
    if (!appId) return;

    void read(steamIds, async () => {
      const result = await trackedProviders();
      return result?.ok && result.steam_ids ? result.steam_ids : {};
    }).then(async (map) => {
      const steamId = map[appId];
      if (!steamId || cancelled) return;
      const answer = await currentPlayers(steamId).catch(() => undefined);
      if (!cancelled && answer?.ok && typeof answer.players === "number") {
        setPlayers(answer.players);
      }
    });
    return () => { cancelled = true; };
  }, [appId]);

  return players;
}
