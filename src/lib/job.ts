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
  if (clearTimer) clearTimeout(clearTimer);
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
  job = IDLE;
  listeners.clear();
}
