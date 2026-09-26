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
interface DeckySocket {
  ws?: { readyState?: number };
  connect?: () => void;
}

function backend(): DeckySocket | undefined {
  try {
    return (window as unknown as { DeckyBackend?: DeckySocket }).DeckyBackend;
  } catch {
    return undefined;
  }
}

export function socketUsable(): boolean {
  const state = backend()?.ws?.readyState;
  if (typeof state !== "number") return true;
  return state === 1;                       // 1 = OPEN
}

/** Ask Decky to open its socket again.
 *
 *  Waiting was not enough, and the measurement says why: this context's
 *  `ws` does not cycle, it stays CLOSED. The reconnects visible in the
 *  loader's journal belong to Steam's OTHER renderer contexts, each holding
 *  its own connection -- ours, once dropped, is simply never rebuilt, and
 *  every call after it waits for a socket that is not coming back.
 *
 *  DeckyBackend.connect() rebuilds it. Verified on hardware: CLOSED before
 *  the call, OPEN after. It is Decky's own method, the one its bootstrap
 *  uses, so this is asking for the reconnect Decky would have done rather
 *  than reaching around it.
 */
function reconnect(): void {
  try {
    backend()?.connect?.();
  } catch {
    /* it either comes back or the wait below gives up */
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

/** How long to wait for a closed socket to come back before giving up. */
const RECONNECT_WAIT = 20000;
/** How often to look while waiting. */
const RECONNECT_POLL = 250;

/** Wait for Decky's socket to come back, or give up after RECONNECT_WAIT.
 *
 *  Measured on hardware: the socket does not stay down, it CYCLES -- ready,
 *  closed, ready again, dozens of times a minute, because several of Steam's
 *  renderer contexts each hold a connection and the router keeps one. Any
 *  single call has a good chance of landing in a closed moment.
 *
 *  Refusing on the spot was honest and useless: it turned a gap of a few
 *  seconds into a failed sync and a panel that could not load. Waiting rides
 *  straight over the gap, which is the difference between a plugin that works
 *  and a plugin that correctly explains why it does not.
 */
async function waitForSocket(): Promise<boolean> {
  if (socketUsable()) return true;
  const until = Date.now() + RECONNECT_WAIT;
  let asked = 0;
  while (Date.now() < until) {
    // Ask, then wait; and ask again every couple of seconds, because the
    // first attempt can land while the loader is still tearing the old
    // connection down.
    if (Date.now() >= asked) {
      reconnect();
      asked = Date.now() + 2000;
    }
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_POLL));
    if (socketUsable()) return true;
  }
  return false;
}

/** How long any single backend call may stay outstanding.
 *
 *  Not a performance budget -- it is far longer than any call here takes. It
 *  exists so that a call which never comes back cannot cost a SLOT forever.
 *
 *  That distinction is the whole freeze. withTimeout gives up WAITING on a
 *  promise, but it cannot cancel it, so the abandoned call still sits inside
 *  withSlot holding its permit. Four of those and every later call -- the
 *  panel's get_status included -- queues behind permits that will never come
 *  back. The plugin does not fail at that point, it goes quiet, which is
 *  precisely what "stuck on Loading..." was.
 *
 *  So the slot is released on a deadline too, and the call is rejected rather
 *  than left pending: the callers all treat a failed call as "skip it and
 *  carry on", and an error names itself in the log where silence names
 *  nothing. */
const CALL_DEADLINE = 60000;

/** The largest argument this will put on the wire.
 *
 *  Decky carries these over aiohttp, which CLOSES a websocket on a message
 *  over 4 MB instead of raising. Nothing reports it: the socket simply stops,
 *  every outstanding promise stays pending forever, and the log ends
 *  mid-sentence. Two separate freezes were this, a hero image and then an
 *  icon, and each cost a day because the failure erases its own evidence.
 *
 *  Refusing loudly under the cap turns a silenced plugin into one failed call
 *  with a message that says which one and how big. Anything genuinely this
 *  large belongs in slices -- see ARTWORK_CHUNK_CHARS -- or should be named by
 *  URL and fetched on the Python side, which is what the icon does now. */
const MAX_ARG_CHARS = 3 * 1024 * 1024;

function tooBig(name: string, args: unknown[]): string | undefined {
  for (const arg of args) {
    if (typeof arg === "string" && arg.length > MAX_ARG_CHARS) {
      return `${name}: refusing to send ${Math.round(arg.length / 1024)} KB `
        + `in one message (limit ${Math.round(MAX_ARG_CHARS / 1024)} KB) — `
        + "this would close Decky's socket silently";
    }
  }
  return undefined;
}

/** Reject if `work` has not settled in `ms`, so its slot is always given up. */
function onADeadline<T>(work: Promise<T>, ms: number, name: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${name} did not answer in ${ms / 1000}s`)), ms);
    const clear = () => clearTimeout(timer);
    work.then((value) => { clear(); resolve(value); },
              (error) => { clear(); reject(error); });
  });
}

/** A backend method, rate-limited and ridden over a socket that keeps
 *  cycling. */
export function guarded<A extends unknown[], R>(name: string): (...args: A) => Promise<R> {
  const call = callable<A, R>(name);
  return (...args: A): Promise<R> =>
    withSlot(async () => {
      // Checked before the socket, because this is the one failure that takes
      // the socket WITH it. A refusal here costs one call; sending it costs
      // the connection and every call still waiting on it.
      const oversized = tooBig(name, args);
      if (oversized) return Promise.reject(new Error(oversized));
      if (!socketUsable() && !(await waitForSocket())) {
        // Thrown rather than left pending. The callers already treat a failed
        // call as "skip this and carry on", and an error that says why beats
        // a sync that stops with no reason recorded anywhere.
        //
        // It says what to DO, because nothing here can fix it: Decky exposes
        // no way to reopen this socket, and it does not reopen itself. An
        // error that only says "unreachable" leaves you pressing Sync at
        // something that can never answer.
        return Promise.reject(new Error(
          "Decky's connection to the plugin has been down for "
          + `${RECONNECT_WAIT / 1000}s \u2014 reload Shelfcloud in Decky `
          + `\u203a Settings \u203a Plugins. (${name})`));
      }
      try {
        return await onADeadline(call(...args), CALL_DEADLINE, name);
      } catch (error) {
        // One retry, and only for a call that failed WITH the socket. Decky
        // drops the in-flight call when the connection goes, so this is the
        // difference between a sync surviving a five second gap and a sync
        // ending on one. A call that failed for its own reasons is not
        // retried -- the socket is still up, so the error is real.
        if (socketUsable()) throw error;
        if (!(await waitForSocket())) throw error;
        return await onADeadline(call(...args), CALL_DEADLINE, name);
      }
    });
}
