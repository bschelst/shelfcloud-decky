// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Shapes exchanged with the plugin backend (main.py).

/** Artwork per Steam library slot, already chosen by the backend. */
export interface Artwork {
  capsule?: string | null;   // 600x900 portrait — the small library grid tile
  header?: string | null;    // 460x215 — the wide banner of the selected game
  hero?: string | null;      // 1920x620 background on the game page
  logo?: string | null;      // transparent logo over the hero
  // the game's own picture, used to draw a capsule when it has none (wide).
  cover?: string | null;
  // not a library slot: Steam keeps an icon as a path in shortcuts.vdf. square, not the logo.
  icon?: string | null;
}

/** A game the library should contain, with its launch command already built. */
export interface DesiredGame {
  // how this tile is recorded: "<game id>:<provider>". with sync_all_providers on a
  // game gets one tile per service, all with the same game_id. optional for an old backend.
  key?: string;
  game_id: number;
  // the game's id on Steam, where it has one. absent for the ~17% not on Steam.
  steam_app_id?: string | number | null;
  name: string;
  provider: string;
  exe: string;
  options: string;
  // where this game launches when the gateway can not be reached. kept in our own state,
  // never on the Steam command line (Steam shows launch options verbatim).
  url?: string;
  artwork?: Artwork;
  // the same slots served back through the gateway, for art whose host sends no CORS header.
  artwork_proxy?: Artwork;
  // Steam Input template applied on first install. resolved server-side: per-game > provider > null.
  controller_layout?: string | null;
}

/** A shortcut this plugin created, so it can be updated or removed later. */
export interface TrackedShortcut {
  app_id: number;
  name: string;
  launch: string;
  // fingerprint of the applied artwork, so better art can be spotted later.
  art?: string;
  // which service this tile opens. absent on an entry written before per-provider tiles.
  provider?: string;
}

export interface LibraryResponse {
  ok: boolean;
  signed_in?: boolean;
  desired?: DesiredGame[];
  tracked?: Record<string, TrackedShortcut>;
  skipped?: number;
  error?: string;
  // a hash of `desired` + `tracked`. hand it back next time.
  version?: string;
  // the answer is the one you already have, so it was not sent again.
  unchanged?: boolean;
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

// Progress of a browser install. kept in the backend so closing the panel loses it not.
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
  // plugin version, read from package.json. "unknown" when it could not be read.
  version?: string;
  steam_id64?: string | null;
  tracked: number;
  browser: Browser | null;
  browsers: Browser[];
  // offered when `browsers` is empty; already-installed ones are filtered out.
  installable?: InstallableBrowser[];
  install?: InstallState;
  // set while a sign-in waits: the code the return page asks for. kept in the backend
  // because opening Steam's browser closes the panel.
  pending_login_code?: string;
  sync_minutes: number;
  sync_choices: number[];
  // make a tile per service a game is on, not only the best one. off by default, it grows the library.
  sync_all_providers?: boolean;
  // how much to scale a provider's web interface: "auto", or a number. a cloud UI is small on a Deck.
  ui_scale?: string | number;
  // what was detected, in words, shown under the dropdown so "Auto" is not a bare number.
  ui_scale_detected?: string;
  // show a mark on a cloud game's page saying which service streams it. on by default.
  badge_enabled?: boolean;
  // the same mark on the card in the home carousel and the grid. its own switch.
  badge_on_cards?: boolean;
  // where it sits: "tl", "tm", "tr", "bl", "bm" or "br".
  badge_position?: string;
  // show how many people are playing, from Steam. off by default.
  players_enabled?: boolean;
  // where that one sits. same six positions.
  players_position?: string;
  by_provider: Record<string, number>;
  last_error?: string | null;
}

export interface LoginStart {
  ok: boolean;
  session_token?: string;
  login_url?: string;
  // pairing code typed on the page Steam returns to, so someone else can not finish
  // a sign-in for you.
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
  // names of the newly added games, so a notification can say wich, not just how many.
  addedNames: string[];
  // true when the run was asked to stop, not because it ran out. the counts above still hold.
  cancelled?: boolean;
}

// How each provider reads for a person. THE ONE PLACE: a provider missing here ships the raw key.
const PROVIDER_LABELS: Record<string, string> = {
  boosteroid: "Boosteroid",
  geforce_now: "GeForce NOW",
  xbox_cloud: "Xbox Cloud",
  amazon_luna: "Amazon Luna",
  playstation: "PlayStation",
  shadow: "Shadow",
  airgpu: "AirGPU",
  // the two website providers, not cloud services: our own page, and one the user added.
  shelfcloud: "Shelfcloud",
  // plural: the collection of pages you added, so it reads as a heading.
  custom: "Websites",
  // tiles recorded before providers were tracked. fixed by the next sync.
  unknown: "Other",
};

// Providers that are a web page, not a cloud service: no provider suffix, one collection.
export const WEBSITE_PROVIDERS = new Set(["shelfcloud", "custom"]);

// every provider this build can name, for tests that check nothing is missed.
export const KNOWN_PROVIDERS = Object.keys(PROVIDER_LABELS);

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

// Launch command for a Steam tile that opens the Shelfcloud website.
export interface WebsiteLaunch {
  exe: string;
  options: string;
  name: string;
  browser: string;
}

// How many owned-on-Steam games could be bulk-added.
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
