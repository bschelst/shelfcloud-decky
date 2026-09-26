// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which panel section is open, as arithmetic rather than as a component.
//
// Settings, Links and About are each long enough that two open at once push
// whatever is below them off a 7-inch screen, so the control you were
// reaching for moves while you reach for it. One at a time keeps the panel
// roughly the same height however you got there.
//
// It lives here rather than beside the component because the component cannot
// be exercised in the test suite -- importing it pulls in @decky/ui, which
// wants a `window` this suite does not have. Same split as the logo and badge
// layouts: the rule is testable, the rendering around it is not.

/** The section open after `activated` is pressed, given what is open now.
 *
 *  Pressing the section that is already open CLOSES it, rather than doing
 *  nothing. Otherwise the last one opened can never be put away and the panel
 *  keeps its tallest shape for the rest of the session.
 */
export function nextOpenId(current: string | null, activated: string): string | null {
  return current === activated ? null : activated;
}
