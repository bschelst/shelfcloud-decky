// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Whether an address from outside is safe to hand to Steam. In lib/ so it can be
// tested (index.tsx imports @decky/ui, which only loads inside Steam).

/**
 * Is this safe to pass to Steam's URL handler? It gets pasted into
 * `steam://openurl/${url}`, which Steam parses as a command, and it comes from
 * the gateway unchecked. So:
 *   - https only (http could rewrite where a sign-in goes)
 *   - a real hostname, ruling out `https:///x` and opaque schemes
 *   - no whitespace or control chars, which could end one steam:// command and
 *     start another
 */
export function isSafeExternalUrl(url: unknown): url is string {
  if (typeof url !== "string" || !url) return false;
  // Escaped, not typed: literal control bytes in a source file break grep and
  // diffs.
  // eslint-disable-next-line no-control-regex
  if (/[\s\u0000-\u001f\u007f]/.test(url)) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !!parsed.hostname;
  } catch {
    return false;                      // not a URL at all
  }
}
