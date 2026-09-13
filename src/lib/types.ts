/** Shapes exchanged with the plugin backend (main.py). */

/** Artwork per Steam library slot, already chosen by the backend. */
export interface Artwork {
  capsule?: string | null;   // 600x900 portrait — the small library grid tile
  header?: string | null;    // 460x215 — the wide banner of the SELECTED game
  hero?: string | null;      // 1920x620 background on the game page
  logo?: string | null;      // transparent logo overlaid on the hero
}

/** A game the library should contain, with its launch command already built. */
export interface DesiredGame {
  game_id: number;
  name: string;
  provider: string;
  exe: string;
  options: string;
  artwork?: Artwork;
}

/** A shortcut this plugin created, so it can be updated or removed later. */
export interface TrackedShortcut {
  app_id: number;
  name: string;
  launch: string;
  /** Fingerprint of the artwork applied, so better art can be detected later. */
  art?: string;
}

export interface LibraryResponse {
  ok: boolean;
  signed_in?: boolean;
  desired?: DesiredGame[];
  tracked?: Record<string, TrackedShortcut>;
  skipped?: number;
  error?: string;
}

export interface Browser {
  app_id: string;
  label: string;
}

export interface Status {
  signed_in: boolean;
  steam_id64?: string | null;
  gateway_url: string;
  tracked: number;
  browser: Browser | null;
  browsers: Browser[];
  sync_minutes: number;
  sync_choices: number[];
  last_error?: string | null;
}

export interface LoginStart {
  ok: boolean;
  session_token?: string;
  login_url?: string;
  error?: string;
}

export interface LoginPoll {
  ok: boolean;
  status?: "pending" | "signed_in";
  steam_id64?: string;
  error?: string;
}

export interface SyncResult {
  added: number;
  removed: number;
  updated: number;
  failed: number;
}

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

/** Launch command for a Steam tile that opens the Shelfcloud website. */
export interface WebsiteLaunch {
  exe: string;
  options: string;
  name: string;
  browser: string;
}

/** How many owned-on-Steam games could be bulk-added. */
export interface OwnedPreview {
  ok: boolean;
  available?: number;
  not_added?: number;
  error?: string;
}

export interface AddOwnedResult {
  ok: boolean;
  added?: number;
  error?: string;
}

export interface SteamLibraryResult {
  ok: boolean;
  owned?: number;
  status?: string;
  message?: string;
  error?: string;
}
