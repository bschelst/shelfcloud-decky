/** Shapes exchanged with the plugin backend (main.py). */

/** A game the library should contain, with its launch command already built. */
export interface DesiredGame {
  game_id: number;
  name: string;
  provider: string;
  exe: string;
  options: string;
  cover_url?: string | null;
  banner_url?: string | null;
}

/** A shortcut this plugin created, so it can be updated or removed later. */
export interface TrackedShortcut {
  app_id: number;
  name: string;
  launch: string;
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
