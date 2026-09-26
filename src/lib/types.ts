// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

/** Shapes exchanged with the plugin backend (main.py). */

/** Artwork per Steam library slot, already chosen by the backend. */
export interface Artwork {
  capsule?: string | null;   // 600x900 portrait — the small library grid tile
  header?: string | null;    // 460x215 — the wide banner of the SELECTED game
  hero?: string | null;      // 1920x620 background on the game page
  logo?: string | null;      // transparent logo overlaid on the hero
  /** The game's own picture, used to DRAW a capsule when it has none.
   *  Wide, so it is composed onto a portrait rather than stretched into one. */
  cover?: string | null;
  // Not a library slot: Steam stores an icon as a path in shortcuts.vdf and
  // draws it in collections and list views. Square, and deliberately not the
  // logo — a logo replaces the title, an icon replaces nothing.
  icon?: string | null;
}

/** A game the library should contain, with its launch command already built. */
export interface DesiredGame {
  /** How this tile is recorded: "<game id>:<provider>".
   *
   *  A game is not always one tile. With sync_all_providers on it gets one per
   *  service it can be played on, and those all carry the same game_id — so a
   *  game id no longer identifies a tile on its own. Optional only so an older
   *  backend that does not send one still syncs. */
  key?: string;
  game_id: number;
  /** The game's id on STEAM, where it has one. Absent for the ~17% that
   *  are not on Steam at all. */
  steam_app_id?: string | number | null;
  name: string;
  provider: string;
  exe: string;
  options: string;
  /** Where this game launches when the gateway cannot be reached.
   *  Recorded in the plugin's own state, never on the Steam command line —
   *  Steam shows launch options verbatim in a game's properties. */
  url?: string;
  artwork?: Artwork;
  /** The same slots served back through the gateway, for art whose own host
   *  sends no CORS header and so cannot be fetched from inside Steam. */
  artwork_proxy?: Artwork;
  /** Steam Input template URL to apply on first install, so the Deck's
   *  controls match what the tile opens. Resolved server-side: per-game
   *  override > provider default > null (leave Steam's own default). */
  controller_layout?: string | null;
}

/** A shortcut this plugin created, so it can be updated or removed later. */
export interface TrackedShortcut {
  app_id: number;
  name: string;
  launch: string;
  /** Fingerprint of the artwork applied, so better art can be detected later. */
  art?: string;
  /** Which service this tile opens. Absent on an entry written before tiles
   *  were recorded per provider, which is what makes it worth checking. */
  provider?: string;
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
  /** Set while a sign-in is waiting: the code the return page asks for.
   *  Kept in the backend because opening Steam's browser closes the
   *  panel that first displayed it. */
  pending_login_code?: string;
  sync_minutes: number;
  sync_choices: number[];
  /** Make a tile per service a game is on, rather than only the best one.
   *  Off unless turned on: it multiplies the size of the library. */
  sync_all_providers?: boolean;
  /** How much to scale a provider's web interface: "auto", or a number.
   *  A cloud service's UI is built for a monitor at arm's length and is small
   *  on a 7" handheld, smaller still across a room on a television. */
  ui_scale?: string | number;
  /** What was detected, in words — shown under the dropdown so "Auto" is not
   *  a number the user has to take on trust. */
  ui_scale_detected?: string;
  /** Show a mark on a cloud game's page saying which service streams it.
   *  On unless turned off. */
  badge_enabled?: boolean;
  /** The same mark on the highlighted card in the home carousel and the
   *  grid. Its own switch: two places, and wanting one without the other is
   *  an ordinary thing to want. */
  badge_on_cards?: boolean;
  /** Where it sits: "tl", "tm", "tr", "bl", "bm" or "br". */
  badge_position?: string;
  /** Show how many people are playing, from Steam. Off by default. */
  players_enabled?: boolean;
  /** Where that one sits. Same six positions. */
  players_position?: string;
  by_provider: Record<string, number>;
  last_error?: string | null;
}

export interface LoginStart {
  ok: boolean;
  session_token?: string;
  login_url?: string;
  /** Pairing code that must be typed on the page Steam returns to.
   *  Without it a sign-in cannot be completed — it is what stops
   *  someone else finishing a session they started for you. */
  confirm_code?: string;
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
  /** True when the run stopped because it was asked to, rather than because it
   *  ran out of games. The counts above still describe what it did reach: a
   *  cancelled run is a shorter run, not a failed one. */
  cancelled?: boolean;
}

/** How each provider is written for a person to read.
 *
 * THE ONE PLACE. This name goes into the Steam tile's title — "Fortnite
 * (Amazon Luna)" — so a provider missing here does not fail, it ships: the
 * fallback returns the raw key and the tile reads "Fortnite (amazon_luna)",
 * which is exactly what happened when Luna was added.
 *
 * The plugin briefly had two of these maps, one here and one in theme.ts, and
 * adding a provider to the other one is what made the miss invisible. theme.ts
 * now keeps only colours and reads its labels from here.
 */
const PROVIDER_LABELS: Record<string, string> = {
  boosteroid: "Boosteroid",
  geforce_now: "GeForce NOW",
  xbox_cloud: "Xbox Cloud",
  amazon_luna: "Amazon Luna",
  playstation: "PlayStation",
  shadow: "Shadow",
  airgpu: "AirGPU",
  // The two website providers, which are not cloud services at all: a page
  // published by Shelfcloud, and a page the user added themselves. Missing
  // here, they fell through to the raw key exactly as the note above warns —
  // a tile reading "Shelfcloud Website (shelfcloud)" and, worse, a Steam
  // collection literally called "custom (Shelfcloud)".
  shelfcloud: "Shelfcloud",
  // Plural: this is the collection of pages you added, and it reads as a
  // heading rather than as a description of one of them.
  custom: "Websites",
  // Tiles recorded before providers were tracked. Fixed by the next sync.
  unknown: "Other",
};

/** Providers that are a web page rather than a cloud service.
 *
 * They are named apart because both places that render a provider want to
 * treat them differently: a page's title needs no provider suffix, and both
 * belong in one collection rather than two.
 */
export const WEBSITE_PROVIDERS = new Set(["shelfcloud", "custom"]);

/** Every provider this build can name, for tests that check nothing is missed. */
export const KNOWN_PROVIDERS = Object.keys(PROVIDER_LABELS);

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
