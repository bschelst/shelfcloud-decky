// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What a long operation is doing, held outside React.
//
// Closing the Quick Access panel unmounts the whole component tree, so
// anything kept in useState is destroyed. The work itself carries on — it is a
// promise, not a component — but its progress used to be written into
// component state, so reopening the panel showed a fresh, idle UI while
// "Add all my games" was still running underneath: no progress, no busy
// indicator, and the button inviting you to start a second run.
//
// Living at module scope, this survives every open and close for the life of
// the session. The panel subscribes when it mounts and picks up whatever is
// already in flight.

export type Job = {
  busy: boolean;
  message: string;
  /** Fraction 0..1 while syncing, for the header bar. */
  progress: number | null;
  /** Bumped when a job finishes, so an open panel knows to refresh its counts. */
  completed: number;
};

const IDLE: Job = { busy: false, message: "", progress: null, completed: 0 };

let job: Job = IDLE;
const listeners = new Set<() => void>();

/** Replace the job snapshot and wake every subscriber.
 *
 * A new object each time, never a mutation: useSyncExternalStore compares
 * snapshots by identity, so mutating in place would leave the panel showing
 * stale text forever.
 */
export function setJob(patch: Partial<Job>): void {
  // A run that is STARTING must start un-cancelled, wherever it was started
  // from. Cancellation belongs to one run, and it used to be cleared only by
  // runJob — which doSync and doAddOwned do not go through. So one press of
  // Cancel left the flag set for the life of the session and every later sync
  // stopped at its first tile: the panel reported success, and the library
  // stayed at however many tiles existed when Cancel was pressed.
  if (patch.busy === true && !job.busy) cancelling = false;
  job = { ...job, ...patch };
  listeners.forEach((listener) => listener());
}

export function subscribeJob(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getJob(): Job {
  return job;
}

/** Mark a job finished, and let any open panel re-read its counts. */
let clearTimer: ReturnType<typeof setTimeout> | null = null;

export function finishJob(message?: string): void {
  // The cancellation belonged to the run that just ended, so it ends with it.
  //
  // It used to be cleared only by runJob, and by setJob on a false->true busy
  // transition. The BACKGROUND sync goes through neither: the poll calls
  // runSync directly. So one press of Cancel left the flag set for the life of
  // the Steam session, and every background sync afterwards stopped at its
  // first tile -- silently, because stopping early is a normal outcome.
  cancelling = false;
  if (clearTimer) clearTimeout(clearTimer);
  // A run that ended on its own must not be reset again ten seconds later.
  if (forceTimer) { clearTimeout(forceTimer); forceTimer = null; }
  setJob({
    busy: false,
    progress: null,
    completed: job.completed + 1,
    ...(message === undefined ? {} : { message }),
  });
  if (message) {
    clearTimer = setTimeout(() => {
      setJob({ message: "" });
      clearTimer = null;
    }, 8000);
  }
}

/** Reset to idle. Tests only — the plugin never goes back to a clean slate. */
export function resetJob(): void {
  if (forceTimer) { clearTimeout(forceTimer); forceTimer = null; }
  onAbandoned = null;
  job = IDLE;
  cancelling = false;
  listeners.clear();
}


// ----------------------------------------------------------------- cancelling

// "Add all my games" walks a whole Steam library, which on a large account is
// thousands of tiles and many minutes. There was no way to stop it: the only
// exits were letting it finish or killing Steam, and closing the panel does
// not help because the run deliberately survives that.
let cancelling = false;

/** Told when Cancel gives up waiting and resets the panel regardless.
 *
 *  The run itself is still going at that point -- it is unreachable, not
 *  stopped -- so whoever holds it has to let go of it. Without that, the next
 *  sync ADOPTS the abandoned run through the shared in-flight promise and
 *  reports its outcome as its own, which is how a cancelled run came back to
 *  life and announced itself as a fresh one.
 *
 *  A callback rather than an import, because the holder is index.tsx and this
 *  module is imported BY it. */
let onAbandoned: (() => void) | null = null;

export function setAbandonHandler(handler: (() => void) | null): void {
  onAbandoned = handler;
}

/** How long Cancel waits to be honoured before it resets the panel anyway. */
const FORCE_IDLE_AFTER = 10000;
let forceTimer: ReturnType<typeof setTimeout> | null = null;

/** Ask the running job to stop at its next clean point.
 *
 *  ...and if nobody is listening, stop waiting for them.
 *
 *  Asking politely is right while something is actually running: it stops at a
 *  tile boundary and leaves the library consistent. But it assumes a loop is
 *  going round to notice, and that assumption fails exactly when it matters.
 *  A backend call that never returns leaves `await` suspended forever, so the
 *  run's own success AND failure paths are both unreachable, `busy` is never
 *  cleared, and every button in the panel stays disabled. The store is at
 *  module scope so closing the panel does not help either: it is stranded for
 *  the life of the Steam session, with nothing running to cancel.
 *
 *  That happened -- a sync blocked writing thousands of controller layouts,
 *  and Cancel could not free it because Cancel was a flag the blocked code
 *  was never going to read. So Cancel now has a deadline: honoured normally,
 *  and after ten seconds it takes the panel back regardless. Worst case a
 *  dead run is still writing somewhere; the single-flight guards stop a
 *  second one starting on top of it, and a usable panel beats a truthful one.
 */
export function cancelJob(): void {
  if (!job.busy) return;
  cancelling = true;
  setJob({ message: "Stopping…" });
  if (forceTimer) clearTimeout(forceTimer);
  forceTimer = setTimeout(() => {
    forceTimer = null;
    if (job.busy && cancelling) {
      // Let go of the run FIRST. finishJob clears `cancelling`, and a new sync
      // starting after that must build its own promise rather than wait on the
      // one nobody can reach.
      try { onAbandoned?.(); } catch { /* never block the reset */ }
      finishJob("Stopped responding, so the panel was reset.");
    }
  }, FORCE_IDLE_AFTER);
}

/** Whether a stop has been asked for. Read by the loops that can honour it. */
export function cancelRequested(): boolean {
  return cancelling;
}

/** Run a long operation, and guarantee the UI is left idle afterwards.
 *
 * Every button in the panel is disabled while `busy`, so a path that returns
 * without clearing it strands the whole UI for the life of the session with
 * nothing actually running. That is not hypothetical: "Remove all games" had
 * four exits and only three of them finished the job, so emptying the library
 * — the one that works — left sync greyed out until the plugin was reloaded.
 *
 * One entry and one exit, so a `return` added inside the body later cannot
 * reintroduce it.
 */
export async function runJob(body: () => Promise<string | void>): Promise<void> {
  cancelling = false;
  setJob({ busy: true, progress: null });
  let closing: string | void;
  try {
    closing = await body();
  } catch (error) {
    closing = String(error);
  } finally {
    cancelling = false;
  }
  finishJob(closing === undefined ? undefined : closing || undefined);
}
