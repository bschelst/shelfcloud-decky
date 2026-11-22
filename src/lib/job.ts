// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// What a long operation is doing, held outside React. Closing the Quick Access
// panel unmounts the component tree, so useState is lost while the work (a
// promise) carries on. living at module scope this survives every open and close
// for the session; the panel subscribes on mount and picks up whatever is running.

export type Job = {
  busy: boolean;
  message: string;
  // fraction 0..1 while syncing, for the header bar.
  progress: number | null;
  // bumped when a job finishes, so an open panel refreshes its counts.
  completed: number;
};

const IDLE: Job = { busy: false, message: "", progress: null, completed: 0 };

let job: Job = IDLE;
const listeners = new Set<() => void>();

// Replace the job snapshot and wake every subscriber. A new object each time,
// never a mutation: useSyncExternalStore compares snapshots by identity, so
// mutating in place would leave the panel showing stale text forever.
export function setJob(patch: Partial<Job>): void {
  // a starting run must start un-cancelled, wherever it was started from.
  // cancelling was cleared only by runJob, which doSync and doAddOwned skip, so
  // one press of Cancel used to stop every later sync on its first tile.
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

// Mark a job finished, and let any open panel re-read its counts.
let clearTimer: ReturnType<typeof setTimeout> | null = null;

export function finishJob(message?: string): void {
  // the cancellation belonged to the run that just ended, so clear it here too.
  // the background sync goes through neither runJob nor setJob's busy transition,
  // so without this one Cancel would stop every later background sync.
  cancelling = false;
  if (clearTimer) clearTimeout(clearTimer);
  // a run that ended on its own must not be reset again ten seconds later.
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

// Reset to idle. Tests only, the plugin never goes back to a clean slate.
export function resetJob(): void {
  if (forceTimer) { clearTimeout(forceTimer); forceTimer = null; }
  onAbandoned = null;
  job = IDLE;
  cancelling = false;
  listeners.clear();
}


// ----------------------------------------------------------------- cancelling

// "Add all my games" walks a whole Steam library, thousands of tiles and many
// minutes. it needs a way to stop, and closing the panel doesnt help since the
// run survives that on purpose.
let cancelling = false;

// Called when Cancel gives up waiting and resets the panel anyway. the run is
// still going (unreachable, not stopped), so the holder must let go of it, else
// the next sync adopts the abandoned run and reports its outcome as its own.
// a callback, not an import, because index.tsx holds it and imports this module.
let onAbandoned: (() => void) | null = null;

export function setAbandonHandler(handler: (() => void) | null): void {
  onAbandoned = handler;
}

// How long Cancel waits to be honoured before it resets the panel anyway.
const FORCE_IDLE_AFTER = 10000;
let forceTimer: ReturnType<typeof setTimeout> | null = null;

// Ask the running job to stop at its next clean point, and if nobody is listening
// stop waiting for them. asking politely stops at a tile boundary and leaves the
// library consistent, but it needs a loop going round to notice. a backend call
// that never returns leaves `await` stuck forever, so `busy` is never cleared and
// the whole panel stays disabled. so Cancel has a deadline: after ten seconds it
// takes the panel back regardless. the single-flight guards stop a second run
// starting on top, and a usable panel beats a truthful one.
export function cancelJob(): void {
  if (!job.busy) return;
  cancelling = true;
  setJob({ message: "Stopping…" });
  if (forceTimer) clearTimeout(forceTimer);
  forceTimer = setTimeout(() => {
    forceTimer = null;
    if (job.busy && cancelling) {
      // let go of the run first. finishJob clears `cancelling`, and a new sync
      // after that must build its own promise, not wait on the one nobody reaches.
      try { onAbandoned?.(); } catch { /* never block the reset */ }
      finishJob("Stopped responding, so the panel was reset.");
      // ... but KEEP asking it to stop. A single tile can take up to
      // PER_GAME_TIMEOUT (40s), well past this 10s deadline, so the loop is
      // usually not stuck - just mid-tile. finishJob cleared the flag, which
      // let the tile finish and the loop see "not cancelled" and plough through
      // all of them. Re-assert it so the run breaks at its next tile boundary;
      // that break calls finishJob again and clears it for good. A new run wipes
      // it at setJob(busy) regardless.
      cancelling = true;
    }
  }, FORCE_IDLE_AFTER);
}

// Whether a stop has been asked for. Read by the loops that can honour it.
export function cancelRequested(): boolean {
  return cancelling;
}

// Run a long operation and always leave the UI idle afterwards. every panel
// button is disabled while `busy`, so a path that returns without clearing it
// strands the whole UI. one entry and one exit, so a `return` added inside the
// body later can not bring that back.
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
