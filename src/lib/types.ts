// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org

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
  /** The same slots served back through the gateway, for art whose own host
   *  sends no CORS header and so cannot be fetched from inside Steam. */
  artwork_proxy?: Artwork;
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

/** A browser Shelfcloud offers to install, when none is present. */
export interface InstallableBrowser {
  app_id: string;
  label: string;
  note: string;
  recommended: boolean;
}

/** Progress of a browser install. Lives in the backend rather than in the
 *  panel so closing the panel mid-download does not lose it. */
export interface InstallState {
  running: boolean;
  app_id: string | null;
  label: string | null;
  message: string;
  error: string | null;
  done: boolean;
}

export interface Status {
  signed_in: boolean;
  /** Plugin version, read from package.json by the backend. "unknown" if
   *  package.json could not be read — About shows that rather than a guess. */
  version?: string;
  steam_id64?: string | null;
  tracked: number;
  browser: Browser | null;
  browsers: Browser[];
  /** Offered when `browsers` is empty; already-installed ones are filtered out. */
  installable?: InstallableBrowser[];
  install?: InstallState;
  sync_minutes: number;
  sync_choices: number[];
  by_provider: Record<string, number>;
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
  /** Names of the games newly added, so a notification can say which rather
   *  than just how many. */
  addedNames: string[];
}

const PROVIDER_LABELS: Record<string, string> = {
  boosteroid: "Boosteroid",
  geforce_now: "GeForce NOW",
  xbox_cloud: "Xbox Cloud",
  playstation: "PlayStation",
  shadow: "Shadow",
  airgpu: "AirGPU",
  // Tiles recorded before providers were tracked. Fixed by the next sync.
  unknown: "Other",
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
