// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Wording for the line shown when a sync finishes.
//
// Its own module, with no Decky imports, for two reasons. It is pure string
// and arithmetic work that has nothing to do with SteamClient, and keeping it
// out of sync.ts means it can be tested without resolving @decky/api — which
// the test environment cannot load.

import type { SyncResult } from "./types";

/**
 * The one-line result shown when a sync finishes.
 *
 * `total` must be the WHOLE library — added, unchanged and skipped alike.
 *
 * What shipped before was "385 games — 385 added (7 skipped)", which invites
 * the reader to look for the 7 inside the 385. They are not there: the total
 * was taken after the skipped entries had already been dropped, so the two
 * numbers described different populations while sitting in one sentence.
 */
export function syncSummary(
  total: number,
  result: SyncResult,
  skipped = 0,
): string {
  const skippedNote = skipped ? ` (${skipped} skipped)` : "";
  // A cancelled run must say so. Its counts are true for what it reached, so
  // without this line "385 games — 40 added" reads as the whole library having
  // been considered and only 40 of it needing anything.
  if (result.cancelled) {
    return `Stopped — ${changeSummary(result)}${skippedNote}`;
  }
  return `${total} games — ${changeSummary(result)}${skippedNote}`;
}

/**
 * Just what changed, with no totals.
 *
 * The toast uses this on its own. It can appear over a game the user is
 * playing, so it says what happened and takes no more of the screen than that.
 */
export function changeSummary(result: SyncResult): string {
  const parts: string[] = [];
  if (result.added) parts.push(`${result.added} added`);
  if (result.removed) parts.push(`${result.removed} removed`);
  if (result.updated) parts.push(`${result.updated} updated`);
  if (result.failed) parts.push(`${result.failed} failed`);
  return parts.length ? parts.join(", ") : "already up to date";
}
