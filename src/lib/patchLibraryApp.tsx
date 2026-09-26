// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Putting the provider badge onto Steam's own game page.
//
// Adapted from the ProtonDB plugin, which injects its medal into the same
// route. None of this is guessable from documentation — it is the shape of
// Steam's React tree, found by reading it — so it is taken as it stands
// rather than rediscovered, and it is kept in one small file so that when
// Steam moves things there is one place to fix.
//
// The route is /library/app/:appid. `renderFunc` is the page; the patcher
// walks into it, finds the container that holds the details body, and puts
// the badge in at index 1 — after the header, before the rest — so it is a
// child of the element the hero sits in and can be positioned against it.

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

/* Steam's tree is untyped and its shape is not ours. `unknown` with casts at
 * each step would say no more than this does and read far worse; the whole
 * function is a walk through somebody else's data structure. */
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
        // Steam changed the tree, or this is a page shaped differently.
        // Returning it untouched loses the badge and nothing else.
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
