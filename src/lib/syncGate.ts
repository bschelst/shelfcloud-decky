// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Keeps the background sync away while the panel is open. Its own module, no
// Decky imports, so it can be tested without @decky/api.
//
// No lock file on purpose: this lives in the same JS runtime as the panel, so
// it is gone when Steam's UI reloads and can not be left behind stale. The
// timeout is only for a close that never gets reported.

/** Longest the panel can hold the sync back. Past this the sync runs anyway. */
export const HOLD_MAX_MS = 30 * 60 * 1000;

export function createSyncGate(now: () => number = Date.now) {
  let openedAt: number | null = null;
  let waiting = false;

  return {
    /** The panel is on screen. */
    open(): void {
      openedAt = now();
    },

    /** The panel closed. True when a sync was held back and should run now. */
    close(): boolean {
      openedAt = null;
      const wasWaiting = waiting;
      waiting = false;
      return wasWaiting;
    },

    /** True when the sync must wait. Remembers it, so close() can say so. */
    hold(): boolean {
      if (openedAt === null || now() - openedAt >= HOLD_MAX_MS) return false;
      waiting = true;
      return true;
    },
  };
}
