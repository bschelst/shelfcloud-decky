// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Adds our cloud tiles to the dropdown beside the Play button on a Steam game
// page ("Play from: This Steam Deck / other PC"). Steam has no hook for that
// menu and its component is not exported, so we catch it where every context
// menu passes: the manager's CreateContextMenuInstance. Only the menu carrying
// `overview` + `onStreamingTargetSelected` is touched, everything else goes
// trough as is. If Steam renames those props the entries just dont show up, the
// menu itself keeps working.
//
// Note: Steam only draws that arrow when there is an other streaming client
// (a second PC, a Steam Machine). Without one there is no menu to add to.

import { beforePatch, findModuleChild, MenuItem, MenuSeparator, type Patch } from "@decky/ui";
import { toaster } from "@decky/api";
import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

import { LogoMono } from "../components/Logo";
import { cloudOptionsNow, refreshBadgeData } from "./badgeData";
import { providerInfo } from "./theme";

/* Steam's menu element and manager are untyped and not ours. */
/* eslint-disable @typescript-eslint/no-explicit-any */

/** ELaunchSource._2ftLibraryDetails, launched from a game's details page. The
 *  enum is only a type in @decky/ui, there is no runtime value to import. */
const LAUNCH_FROM_DETAILS = 100;

interface StreamingMenuProps {
  overview: { appid: number };
  onStreamingTargetSelected: (...args: unknown[]) => void;
}

function isStreamingMenu(element: unknown): element is ReactElement<StreamingMenuProps> {
  if (!isValidElement(element)) return false;
  const props = element.props as Partial<StreamingMenuProps> | null;
  return typeof element.type === "function"
    && typeof props?.overview?.appid === "number"
    && typeof props?.onStreamingTargetSelected === "function";
}

/** Start the tile like the Play button on its own page would. */
function launchTile(appId: string): void {
  const id = Number(appId);
  const overview = Number.isSafeInteger(id) ? window.appStore?.GetAppOverviewByAppID(id) : null;
  if (!overview?.gameid) {
    // tile gone since the last sync, nothing to start
    toaster.toast({ title: "Shelfcloud", body: "That cloud tile is not in the library anymore. Sync again." });
    return;
  }
  SteamClient.Apps.RunGame(overview.gameid, "", -1, LAUNCH_FROM_DETAILS);
}

/** Steam's own menu with our entries after it. Renders Steam's component by
 *  calling it, so we get its Menu element and can append to the children. */
function PlayFromMenu({ inner }: { inner: ReactElement<StreamingMenuProps> }): ReactElement | null {
  const render = inner.type as (props: StreamingMenuProps) => ReactElement | null;
  const menu = render(inner.props);
  const options = cloudOptionsNow(inner.props.overview.appid);
  if (!options.length || !isValidElement(menu)) return menu;

  const own = (menu.props as { children?: ReactNode }).children;
  return cloneElement(
    menu,
    undefined,
    ...Children.toArray(own),
    <MenuSeparator key="shelfcloud-separator" />,
    ...options.map((option) => (
      <MenuItem key={`shelfcloud-${option.appId}`} onSelected={() => launchTile(option.appId)}>
        {/* mono logo takes the text colour, so it turns with the focus like Steam's own text */}
        <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5em" }}>
          <LogoMono />
          {providerInfo(option.provider).label} (Shelfcloud)
        </span>
      </MenuItem>
    )),
  );
}

/** Find the object that hands out the per-window context menu manager. */
function findMenuManagerPrototype(): any | null {
  const holder = findModuleChild((module: any) => {
    if (typeof module !== "object" || module === null) return undefined;
    for (const key in module) {
      try {
        if (typeof module[key]?.GetContextMenuManagerFromWindow === "function") return module[key];
      } catch {
        // some exports throw on access, those are not it
      }
    }
    return undefined;
  });
  const manager = holder?.GetContextMenuManagerFromWindow?.(window);
  const proto = manager ? Object.getPrototypeOf(manager) : null;
  return typeof proto?.CreateContextMenuInstance === "function" ? proto : null;
}

/** Install the patch. Returns null when Steam's menu code is not where we look,
 *  in which case the plugin just runs without this feature. */
export function patchPlayMenu(): Patch | null {
  const proto = findMenuManagerPrototype();
  if (!proto) {
    console.warn("[shelfcloud] context menu manager not found, no cloud entries in the Play menu");
    return null;
  }
  // tile map is needed the moment the menu opens, so have it fetched already
  refreshBadgeData();

  return beforePatch(proto, "CreateContextMenuInstance", (args: any[]) => {
    try {
      if (!isStreamingMenu(args[0])) return;
      refreshBadgeData();  // for the next open, if this one was stale
      args[0] = <PlayFromMenu inner={args[0]} />;
    } catch (error) {
      // never break Steam's menu over our extra lines
      console.error("[shelfcloud] play menu patch failed", error);
    }
  });
}
