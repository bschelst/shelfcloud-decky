/** Shapes returned by the Shelfcloud gateway. Mirrors backend/catalog_cache.py. */

export interface GameProvider {
  provider: string;
  provider_game_id: string | null;
  /** Deep link that opens this game on this provider. The point of the app. */
  launch_url: string;
  storefront: string | null;
}

export interface Game {
  id: number;
  name: string;
  /** Null for cloud-only games such as Fortnite, Roblox or Genshin Impact. */
  steam_app_id: string | null;
  igdb_id: number | null;
  release_date: string | null;
  publisher: string | null;
  cover_url: string | null;
  banner_url: string | null;
  providers: GameProvider[];
}

export interface CatalogResult {
  ok: boolean;
  games: Game[];
  cached?: boolean;
  error?: string;
}

export interface Browser {
  app_id: string;
  label: string;
}

export interface Settings {
  gateway_url: string;
  browser: string | null;
}

/** Provider ids are free-form strings from the data; these are display names only. */
const PROVIDER_LABELS: Record<string, string> = {
  boosteroid: "Boosteroid",
  geforce_now: "GeForce NOW",
  xbox_cloud: "Xbox Cloud",
  playstation: "PlayStation",
  shadow: "Shadow",
  airgpu: "AirGPU",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}
