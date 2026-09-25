// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Whether an address from outside is safe to hand to Steam.
//
// In lib/ rather than beside the panel because index.tsx imports @decky/ui,
// which only loads inside Steam — anything living there cannot be tested.

/**
 * Is this safe to pass to Steam's URL handler?
 *
 * The address arrives from the GATEWAY: `login_url` is whatever the server
 * said, and it gets pasted into `steam://openurl/${url}` — a string Steam
 * then parses as a command. Nothing checked it. A value that is not an https
 * address, or one carrying whitespace or a control character, went to that
 * parser exactly as received.
 *
 * The launcher has held its own launch URLs to an allowlist from the start;
 * this is the same rule arriving late at the other door.
 *
 *   - https only. http would let anything on the path rewrite where a
 *     sign-in goes, and every address this opens is already https.
 *   - a hostname that is actually there, which rules out `https:///x` and
 *     the opaque schemes URL() otherwise accepts.
 *   - no whitespace and no control character, because those are what could
 *     end one steam:// command and begin another.
 */
export function isSafeExternalUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url) return false;
  // Escaped rather than typed: literal control bytes in a source file break
  // grep, diffs and every other tool that reads it as text. (They were typed
  // once, and did exactly that.)
  // eslint-disable-next-line no-control-regex
  if (/[\s\u0000-\u001f\u007f]/.test(url)) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !!parsed.hostname;
  } catch {
    return false;                      // not a URL at all
  }
}
