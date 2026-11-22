// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The "play it on an other service" entries, shared by the two places that show
// them: Steam's own Play dropdown (patchPlayMenu) and our button beside Play
// (patchPlayBar). Same look and same launch in both.

import { MenuItem } from "@decky/ui";
import { toaster } from "@decky/api";
import type { ReactElement } from "react";
import { FaCheck } from "react-icons/fa";

import { LogoMono } from "../components/Logo";
import type { CloudOption } from "./cloudOptions";
import { providerInfo } from "./theme";

/** ELaunchSource._2ftLibraryDetails, launched from a game's details page. The
 *  enum is only a type in @decky/ui, there is no runtime value to import. */
const LAUNCH_FROM_DETAILS = 100;

/** Start a tile like the Play button on its own page would. */
export function launchTile(appId: string): void {
  const id = Number(appId);
  const overview = Number.isSafeInteger(id) ? window.appStore?.GetAppOverviewByAppID(id) : null;
  if (!overview?.gameid) {
    // tile gone since the last sync, nothing to start
    toaster.toast({ title: "Shelfcloud", body: "That cloud tile is not in the library anymore. Sync again." });
    return;
  }
  SteamClient.Apps.RunGame(overview.gameid, "", -1, LAUNCH_FROM_DETAILS);
}

/** The service this tile is on, first in the menu with a check, like Steam
 *  marks "This Steam Deck". Picking it just starts this tile. */
export function currentMenuItem(option: CloudOption): ReactElement {
  return (
    <MenuItem key={`shelfcloud-current-${option.appId}`} onSelected={() => launchTile(option.appId)}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5em" }}>
        {/* Steam's blue check, same column as the logo on the other lines */}
        <FaCheck style={{ width: "1em", height: "1em", color: "#1a9fff", flex: "none" }} />
        {providerInfo(option.provider).label} (Shelfcloud)
      </span>
    </MenuItem>
  );
}

/** One menu line per option: the logo, then "Boosteroid (Shelfcloud)". */
export function cloudMenuItems(options: CloudOption[]): ReactElement[] {
  return options.map((option) => (
    <MenuItem key={`shelfcloud-${option.appId}`} onSelected={() => launchTile(option.appId)}>
      {/* mono logo takes the text colour, so it turns with the focus like Steam's own text */}
      <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5em" }}>
        <LogoMono />
        {providerInfo(option.provider).label} (Shelfcloud)
      </span>
    </MenuItem>
  ));
}
