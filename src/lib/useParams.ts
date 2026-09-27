// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which app a patched Steam route is showing. From the ProtonDB plugin. @decky/ui
// re-exports React Router's useParams, but it moves between versions, so there
// are two fallbacks, ending at the URL itself, wich cannot move.

import { useParams as deckyUseParams, findModuleExport } from "@decky/ui";

function fromPathname<T>(): T {
  const match = window.location.pathname.match(/\/app\/(\d+)/);
  return (match ? { appid: match[1] } : {}) as T;
}

function resolve(): <T>() => T {
  try {
    if (typeof deckyUseParams === "function") return deckyUseParams;

    if (typeof findModuleExport === "function") {
      const found = findModuleExport((e: unknown) => {
        try {
          return typeof e === "function"
            && e.toString().length < 200
            && /\.params:\{\}/.test(e.toString());
        } catch {
          return false;
        }
      }) as (<T>() => T) | undefined;
      if (typeof found === "function") return found;
    }
  } catch (error) {
    console.error("[shelfcloud] useParams: could not find Steam's, using the URL", error);
  }
  return fromPathname;
}

export const useParams = resolve();
