// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which of our cloud tiles stream a given Steam game. Used by the Play button's
// dropdown on a Steam game page. No @decky/ui in here so it can be tested.

/** One cloud tile that can play the game. */
export interface CloudOption {
  /** The tile's own app id, the non-Steam shortcut. */
  appId: string;
  /** Which service the tile is for, a provider key. */
  provider: string;
}

/** Steam app ids are plain positive integers. Anything else is not one. */
function asSteamId(value: unknown): string | null {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value > 0 ? String(value) : null;
  }
  if (typeof value === "string" && /^[1-9]\d{0,15}$/.test(value)) return value;
  return null;
}

/** The tiles whose game is `steamAppId` on Steam. `steamIds` maps tile app id
 *  to Steam app id and `providers` tile app id to service, both as the backend
 *  hands them. Sorted on service, so the menu is the same on every open. */
export function cloudOptionsFor(
  steamAppId: string | number,
  steamIds: Readonly<Record<string, string>>,
  providers: Readonly<Record<string, string>>,
): CloudOption[] {
  const wanted = asSteamId(steamAppId);
  if (!wanted) return [];

  return oneEachService(Object.entries(steamIds)
    .filter(([tile, steam]) => String(steam) === wanted && Boolean(providers[tile]))
    .map(([tile]) => ({ appId: tile, provider: providers[tile] })));
}

/** For one of OUR tiles: the same game on the other services you have it on.
 *  Matched on the catalog game id ("2" for Fortnite on every service), so it
 *  works for games that are not on Steam. The tile's own service is left out. */
export function otherServicesFor(
  tileAppId: string | number,
  gameIds: Readonly<Record<string, string>>,
  providers: Readonly<Record<string, string>>,
): CloudOption[] {
  const tile = asSteamId(tileAppId);   // same shape: a plain positive number
  const game = tile ? gameIds[tile] : undefined;
  const own = tile ? providers[tile] : undefined;
  if (!tile || !game || !own) return [];

  return oneEachService(Object.entries(gameIds)
    .filter(([other, id]) => id === game && Boolean(providers[other]) && providers[other] !== own)
    .map(([other]) => ({ appId: other, provider: providers[other] })));
}

/** Sorted on service and one per service. A game can have two tiles on the
 *  same service (seen on the Deck), and "Boosteroid" twice in a menu is just
 *  confusing. Sorted so the menu is the same on every open. */
function oneEachService(options: CloudOption[]): CloudOption[] {
  const sorted = [...options]
    .sort((a, b) => a.provider.localeCompare(b.provider) || a.appId.localeCompare(b.appId));
  return sorted.filter((option, i) => i === 0 || sorted[i - 1].provider !== option.provider);
}
