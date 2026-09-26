// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// In lib/ and on its own because sync.ts imports @decky/api, which only loads
// inside Steam -- and this rule has to be testable. It is also the one place
// the rule may live: main.py writes the layout under this name and the sync
// composes the URL that selects it, so the two must agree exactly.

/** The directory name Steam uses for a shortcut's controller config.
 *
 *  Lower case, minus every character Windows forbids in a filename and the
 *  full stop with them. Must match _layout_key in main.py exactly: the backend
 *  WRITES the layout under this name and this composes the URL that SELECTS
 *  it, so any disagreement is a layout Steam silently never applies.
 *
 *    "R.E.P.O. (Boosteroid)"       -> "repo (boosteroid)"
 *    "BLiNX: The Time Sweeper (…)" -> "blinx the time sweeper (…)"
 *
 *  Derived from Steam's own answers, not guessed: it reproduces the directory
 *  it reported for all 840 tiles on this device. Lower-casing alone left 139
 *  of them broken — every one with a colon, a pipe or a full stop in its name.
 */
export function layoutKey(name: string): string {
  // eslint-disable-next-line no-useless-escape
  return name.toLowerCase().replace(/[\\\/:*?"<>|.]/g, "");
}
