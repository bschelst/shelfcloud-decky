// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Puts the badges onto Steam's own game page. Adapted from the ProtonDB plugin,
// which injects its medal into the same route. This is the shape of Steam's
// React tree, found by reading it, kept in one file so there is one place to fix
// when Steam moves things. The badges go in after the header so they sit inside
// the element the hero is in and can be placed against it.

import {
  afterPatch,
  appDetailsClasses,
  createReactTreePatcher,
  findInReactTree,
} from "@decky/ui";
import { routerHook } from "@decky/api";
import type { ReactElement } from "react";

import PlayersBadge from "../components/playersBadge";
import ProviderBadge from "../components/providerBadge";

/* Steam's tree is untyped and not ours. `unknown` with casts everywhere would
 * read far worse than any. */
/* eslint-disable @typescript-eslint/no-explicit-any */

/** The patch handle, to hand back to removePatch on unload. */
export type LibraryPatch = ReturnType<typeof routerHook.addPatch>;

export function patchLibraryApp(): LibraryPatch {
  return routerHook.addPatch("/library/app/:appid", (tree: any) => {
    const routeProps = findInReactTree(tree, (x: any) => x?.renderFunc);
    if (!routeProps) return tree;

    const patchHandler = createReactTreePatcher(
      [(t: any) => findInReactTree(t, (x: any) => x?.props?.children?.props?.overview)
        ?.props?.children],
      (_: Array<Record<string, unknown>>, ret?: ReactElement) => {
        const container = findInReactTree(
          ret,
          (x: ReactElement) =>
            Array.isArray((x as any)?.props?.children)
            && (x as any)?.props?.className?.includes(appDetailsClasses.InnerContainer),
        );
        // Steam changed the tree, or this page is shaped differently.
        // Returning it untouched only loses the badge.
        if (typeof container !== "object" || container === null) return ret;

        // Two badges, each placed by its own setting, so they can sit in
        // different corners without either knowing about the other.
        (container as any).props.children.splice(1, 0, <ProviderBadge />);
        (container as any).props.children.splice(2, 0, <PlayersBadge />);
        return ret;
      },
    );

    afterPatch(routeProps, "renderFunc", patchHandler);
    return tree;
  });
}

export default patchLibraryApp;
