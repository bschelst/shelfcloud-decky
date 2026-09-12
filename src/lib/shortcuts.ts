// Creating Steam library entries for cloud games.
//
// Steam's client API is only reachable from the frontend, so shortcut creation
// lives here rather than in the Python backend. AddShortcut returns an appId
// immediately and needs no Steam restart, which is what makes the plugin a
// better experience than writing shortcuts.vdf by hand.

import type { Game, GameProvider } from "./types";

/**
 * Steam's library asset slots.
 *
 * @decky/ui declares ELibraryAssetType but does not re-export it from the
 * package root, and deep-importing its dist path would break on any internal
 * reshuffle. These are the same implicit numeric values as the upstream enum
 * (dist/globals/steam-client/App.d.ts).
 */
const LibraryAsset = {
  Capsule: 0,
  Hero: 1,
  Logo: 2,
  Header: 3,
  Icon: 4,
  HeroBlur: 5,
} as const;

type LibraryAssetType = (typeof LibraryAsset)[keyof typeof LibraryAsset];

const FLATPAK = "/usr/bin/flatpak";

/** Where a browser-based cloud game should start from. */
const START_DIR = "/usr/bin/";

/**
 * Build the flatpak launch options that open one game fullscreen.
 *
 * --kiosk gives a chromeless fullscreen window, which is what makes the tile
 * feel like a game rather than a browser. The window size matches the Deck's
 * native resolution; Gamescope scales it when docked.
 */
export function buildLaunchOptions(browserAppId: string, launchUrl: string): string {
  return [
    "run",
    "--branch=stable",
    "--arch=x86_64",
    browserAppId,
    "--window-size=1280,800",
    "--start-fullscreen",
    `--kiosk ${launchUrl}`,
  ].join(" ");
}

/** Pick which provider a game should launch through. */
export function preferredProvider(game: Game, preference?: string[]): GameProvider | undefined {
  if (game.providers.length === 0) return undefined;
  for (const wanted of preference ?? []) {
    const match = game.providers.find((p) => p.provider === wanted);
    if (match) return match;
  }
  return game.providers[0];
}

async function fetchArtworkBase64(url: string): Promise<string | undefined> {
  try {
    const response = await fetch(url);
    if (!response.ok) return undefined;
    const buffer = await response.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  } catch {
    return undefined;
  }
}

export interface AddResult {
  appId: number;
  artworkApplied: boolean;
}

/**
 * Add a cloud game to the Steam library and give it artwork.
 *
 * Artwork failure is deliberately not fatal: a tile with a blank image is still
 * playable, so a broken provider CDN must not stop the game being added.
 */
export async function addGameToSteam(
  game: Game,
  provider: GameProvider,
  browserAppId: string,
): Promise<AddResult> {
  const launchOptions = buildLaunchOptions(browserAppId, provider.launch_url);
  const appId = await SteamClient.Apps.AddShortcut(game.name, FLATPAK, START_DIR, launchOptions);

  // AddShortcut can return 0/undefined if Steam refuses; treat that as failure.
  if (!appId) {
    throw new Error(`Steam did not create a shortcut for ${game.name}`);
  }

  let artworkApplied = false;
  const artwork: Array<[string | null, LibraryAssetType]> = [
    [game.cover_url, LibraryAsset.Capsule],
    [game.banner_url, LibraryAsset.Hero],
  ];

  for (const [url, assetType] of artwork) {
    if (!url) continue;
    const base64 = await fetchArtworkBase64(url);
    if (!base64) continue;
    try {
      const imageType = url.toLowerCase().includes(".png") ? "png" : "jpg";
      await SteamClient.Apps.SetCustomArtworkForApp(appId, base64, imageType, assetType as never);
      artworkApplied = true;
    } catch {
      // keep going; the shortcut itself is what matters
    }
  }

  return { appId, artworkApplied };
}

export async function removeShortcut(appId: number): Promise<void> {
  SteamClient.Apps.RemoveShortcut(appId);
}
