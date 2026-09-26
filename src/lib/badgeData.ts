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

import { currentPlayers, getStatus, trackedProviders } from "./api";

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
/** app id -> the game's id on STEAM, for tiles whose game is on Steam. */
let steamIds = emptyCache<Record<string, string>>({});
let badge = emptyCache<{
  enabled: boolean; onCards: boolean; position: string;
  players: boolean; playersPosition: string;
}>({ enabled: true, onCards: true, position: "bl",
     players: false, playersPosition: "tr" });

/** Throw both answers away, so the next badge asks again.
 *
 *  Called when a sync finishes: tiles have been made or removed, so the
 *  app-id map is stale by definition. */
export function invalidateBadgeData(): void {
  providers = emptyCache(providers.value);
  steamIds = emptyCache(steamIds.value);
  badge = emptyCache(badge.value);
}

/** The last answers, without waiting for anything.
 *
 *  For the focused-card badge, which runs inside a mutation observer: that
 *  fires while the user is moving the highlight and has to decide what to
 *  draw immediately. An await there would draw the badge a frame or two after
 *  the card it belongs to, which looks like a bug.
 *
 *  Starts as "nothing is ours", so the first card focused before the first
 *  fetch returns gets no badge rather than a wrong one. `refreshBadgeData`
 *  fills it in and the observer's next tick picks it up. */
export function badgeDataNow(): {
  providers: Record<string, string>; enabled: boolean; position: string;
} {
  return {
    providers: providers.value,
    // The CARD badge has its own switch. Turning the game-page one off should
    // not silently take this with it, and the reverse: they are two places,
    // and somebody may well want one and not the other.
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
      // Off by default. It asks Steam a question per game page opened, and a
      // count nobody wanted is not worth that.
      players: status?.players_enabled === true,
      playersPosition: status?.players_position ?? "tr",
    };
  });
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
export function useBadgeSettings(): {
  enabled: boolean; onCards: boolean; position: string;
  players: boolean; playersPosition: string;
} {
  const [value, setValue] = useState(badge.value);

  useEffect(() => {
    let cancelled = false;
    void read(badge, async () => {
      const status = await getStatus();
      return {
        enabled: status?.badge_enabled !== false,
        onCards: status?.badge_on_cards !== false,
        position: status?.badge_position ?? "bl",
        players: status?.players_enabled === true,
        // Top right by default, so it does not land on the provider badge,
        // which defaults to the bottom left.
        playersPosition: status?.players_position ?? "tr",
      };
    }).then((next) => { if (!cancelled) setValue(next); });
    return () => { cancelled = true; };
  }, []);

  return value;
}


/** How many people are playing this tile's game on Steam, if anyone can say.
 *
 *  Undefined covers three different "no": the tile is not one of ours, its
 *  game is not on Steam at all (about one in six), or Steam had no answer.
 *  None of those is a zero, and showing "0 playing" for any of them would be
 *  a statement we cannot support.
 *
 *  Not cached here: current_players caches on the backend, where the answer
 *  is shared by every page that asks and a five minute life is right. A
 *  second cache in front of it would only make the number staler.
 */
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
