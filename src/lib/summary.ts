// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Wording for the line shown when a sync finishes. Its own module, no Decky
// imports, so it can be tested without @decky/api (the test env cannot load it).

import type { SyncResult } from "./types";

/**
 * The one-line result shown when a sync finishes. `total` must be the WHOLE
 * library -- added, unchanged and skipped alike. It shipped once as
 * "385 games -- 385 added (7 skipped)", where the 7 were not inside the 385.
 */
export function syncSummary(
  total: number,
  result: SyncResult,
  skipped = 0,
): string {
  const skippedNote = skipped ? ` (${skipped} skipped)` : "";
  // A cancelled run must say so, else "385 games -- 40 added" reads as the whole
  // library seen with only 40 needing anything.
  if (result.cancelled) {
    return `Stopped — ${changeSummary(result)}${skippedNote}`;
  }
  return `${total} games — ${changeSummary(result)}${skippedNote}`;
}

/**
 * Just what changed, no totals. The toast uses this on its own; it can appear
 * over a game the user is playing, so it stays short.
 */
export function changeSummary(result: SyncResult): string {
  const parts: string[] = [];
  if (result.added) parts.push(`${result.added} added`);
  if (result.removed) parts.push(`${result.removed} removed`);
  if (result.updated) parts.push(`${result.updated} updated`);
  if (result.failed) parts.push(`${result.failed} failed`);
  return parts.length ? parts.join(", ") : "already up to date";
}
