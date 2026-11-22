// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// On its own so it can be tested (sync.ts imports @decky/api, Steam-only). This
// name must match main.py exactly: it writes the layout, this selects it.

/** The directory name Steam uses for a shortcut's controller config. Lower case,
 *  minus every character Windows forbids in a filename and the full stop to.
 *  Must match _layout_key in main.py, else Steam silently never applies the
 *  layout.
 *
 *    "R.E.P.O. (Boosteroid)"       -> "repo (boosteroid)"
 *    "BLiNX: The Time Sweeper (…)" -> "blinx the time sweeper (…)"
 */
export function layoutKey(name: string): string {
  // eslint-disable-next-line no-useless-escape
  return name.toLowerCase().replace(/[\\\/:*?"<>|.]/g, "");
}
