// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Settings behaviour that is more than "store what the control says".
//
// In lib/ rather than beside the panel because SettingsSection imports
// @decky/ui, which only loads inside Steam — anything living in that file is
// unreachable from a test.

import type { Status } from "./types";

export interface TilePerServiceDeps {
  setSetting: (key: string, value: unknown) => Promise<Status>;
  setStatus: (s: Status) => void;
  /** Pulls in the games the user can play but has never added. */
  onEnabled?: () => void;
}

/**
 * What happens when "a tile per service" is switched.
 *
 * The ORDER here is the whole behaviour.
 *
 * On: store the setting, wait for it, and only then pull the games in. The
 * fetch ends in a sync, and a sync that read the old value would fan nothing
 * out — the games would arrive in exactly the shape the option was switched
 * on to change.
 *
 * Off: store it and stop. Switching off only takes tiles away; there is
 * nothing to fetch, and a bulk add there would be the opposite of what was
 * asked for.
 *
 * The fetch matters because the option alone is not enough. It fans a game
 * out across every service it plays on, which does nothing for a service
 * whose games were never added: a library holding 8 of Amazon Luna's 119
 * titles gives 8 Luna tiles however this is set. The other 111 are covered by
 * the subscription and nothing had ever asked for them.
 */
export async function applyTilePerService(
  value: boolean,
  deps: TilePerServiceDeps,
): Promise<void> {
  const next = await deps.setSetting("sync_all_providers", value);
  deps.setStatus(next);
  if (value) deps.onEnabled?.();
}
