// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Every call into the Python backend goes through here.
//
// Decky carries these over a single websocket. That socket dies under a burst:
// measured on hardware mid-sync, `DeckyBackend.ws.readyState` was CLOSED with
// 553 calls outstanding and the request counter still climbing, because the
// panel went on posting into a pipe that had no other end. Nothing throws when
// that happens -- the promises simply never settle -- so the plugin does not
// fail, it stops, and every await in the sync hangs at once. Seven separate
// attempts to find "the call that hangs" were looking for a single culprit
// when the answer was all of them, from the moment the socket went.
//
// Two rules, both of them about not doing that again:
//
//   1. Look before posting. A CLOSED socket is an error now, raised in
//      milliseconds and visible in the log, instead of a promise nobody ever
//      hears from. A dead channel should be loud.
//
//   2. Do not post faster than it can answer. The calls were fired as fast as
//      a loop could issue them; they queue here instead, a few at a time. The
//      same work reaches the backend, at a rate that does not bury it.

import { callable } from "@decky/api";

/** How many backend calls may be in flight at once.
 *
 *  Small deliberately. These are local and quick, so a handful in parallel is
 *  already more than enough to keep the socket busy -- and the failure this
 *  exists to prevent came from having hundreds outstanding at once. */
const MAX_IN_FLIGHT = 4;

let active = 0;
const waiting: Array<() => void> = [];

/** Whether Decky's socket can still carry a call.
 *
 *  Reads the loader's own websocket rather than keeping a guess of our own.
 *  When it cannot be found -- a Decky version that arranges this differently,
 *  or a test -- the answer is "yes": refusing to call on the strength of a
 *  missing internal would break the plugin for a detail it cannot see.
 */
export function socketUsable(): boolean {
  try {
    const backend = (window as unknown as {
      DeckyBackend?: { ws?: { readyState?: number } };
    }).DeckyBackend;
    const state = backend?.ws?.readyState;
    if (typeof state !== "number") return true;
    return state === 1;                     // 1 = OPEN
  } catch {
    return true;
  }
}

/** How many calls are queued or running, for tests and diagnostics. */
export function rpcLoad(): { active: number; waiting: number } {
  return { active, waiting: waiting.length };
}

async function withSlot<T>(run: () => Promise<T>): Promise<T> {
  if (active >= MAX_IN_FLIGHT) {
    await new Promise<void>((resolve) => waiting.push(resolve));
  }
  active += 1;
  try {
    return await run();
  } finally {
    active -= 1;
    waiting.shift()?.();
  }
}

/** A backend method, rate-limited and refused outright on a dead socket. */
export function guarded<A extends unknown[], R>(name: string): (...args: A) => Promise<R> {
  const call = callable<A, R>(name);
  return (...args: A): Promise<R> =>
    withSlot(() => {
      if (!socketUsable()) {
        // Thrown rather than left pending. The callers already treat a failed
        // call as "skip this and carry on", and an error that says why beats
        // a sync that stops with no reason recorded anywhere.
        return Promise.reject(
          new Error(`backend unreachable: Decky's socket is closed (${name})`));
      }
      return call(...args);
    });
}
