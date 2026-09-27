// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Settings behaviour that is more than "store what the control says". in lib/ and
// not next to the panel because SettingsSection imports @decky/ui, which only
// loads inside Steam, so anything in that file can not be reached from a test.

import type { Status } from "./types";

export interface TilePerServiceDeps {
  setSetting: (key: string, value: unknown) => Promise<Status>;
  setStatus: (s: Status) => void;
  // pulls in the games the user can play but has never added.
  onEnabled?: () => void;
  // whether a sync or bulk add is already running. the setting is stored either
  // way; only the fetch that follows is held back.
  busy?: boolean;
}

// What happens when "a tile per service" is switched. the ORDER is the whole
// behaviour. on: store the setting, wait for it, then pull the games in, because a
// sync reading the old value would fan nothing out. off: store it and stop, since
// switching off only takes tiles away. the fetch matters because the option alone
// only fans out games already added: a library with 8 of Luna's 119 titles stays
// at 8 tiles until the other 111 are fetched.
export async function applyTilePerService(
  value: boolean,
  deps: TilePerServiceDeps,
): Promise<void> {
  // the setting is stored first and always, whatever else is going on. it used to
  // be reachable only when nothing ran, so a tap during a sync did nothing.
  const next = await deps.setSetting("sync_all_providers", value);
  deps.setStatus(next);
  // the games that follow can wait; the answer to "is this on" can not. if a run
  // is in flight the fetch is skipped and that sync picks the setting up next pass.
  if (value && !deps.busy) deps.onEnabled?.();
}
