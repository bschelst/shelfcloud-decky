// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which panel section is open, as arithmetic, not a component -- so it can be
// tested (the component pulls in @decky/ui, which wants a `window` the test
// suite lacks). One section open at a time keeps the panel a steady height on a
// 7-inch scherm.

/** The section open after `activated` is pressed, given what is open now.
 *  Pressing the open one closes it, so the last opened can be put away again. */
export function nextOpenId(current: string | null, activated: string): string | null {
  return current === activated ? null : activated;
}
