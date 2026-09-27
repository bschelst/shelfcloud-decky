// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Every call into the Python backend goes through here. Two rules that both
// exist because a burst over Decky's single websocket kills it silently (4 MB
// message cap, no throw, promises never settle): check the socket before
// posting, and don't post faster than it can answer. See
// docs/RPC-PAYLOAD-LIMITS.md before adding an RPC.

import { callable } from "@decky/api";

// kept small: these calls are local and fast, and the freeze we guard against
// came from having hundreds open at once.
const MAX_IN_FLIGHT = 4;

// one slot is always kept free for the panel, so get_status does not wait behind
// a sync that holds every permit for the fifty seconds it runs.
const MAX_BACKGROUND = MAX_IN_FLIGHT - 1;

let active = 0;
const waiting: Array<() => void> = [];

// Reads the loader's own socket. When we can not find it (other Decky version,
// or a test) we say yes, so a missing detail does not break the plugin.
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

// Ask Decky to open its socket again. Just waiting does not help: our own `ws`
// stays CLOSED and is never rebuilt on its own. connect() rebuilds it, tested on
// hardware (closed before, open after). It is Decky's own method.
function reconnect(): void {
  try {
    backend()?.connect?.();
  } catch {
    /* it either comes back or the wait below gives up */
  }
}

// how many calls are queued or running, for tests and diagnostics.
export function rpcLoad(): { active: number; waiting: number } {
  return { active, waiting: waiting.length };
}

async function withSlot<T>(run: () => Promise<T>,
                           background = false): Promise<T> {
  const ceiling = background ? MAX_BACKGROUND : MAX_IN_FLIGHT;
  // `while`, not `if`: a caller can slip into the gap between waking a waiter
  // and it taking the slot, pushing us over the limit. re-checking on wake
  // stops that.
  while (active >= ceiling) {
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

const RECONNECT_WAIT = 20000; // how long to wait for a closed socket to return
const RECONNECT_POLL = 250; // how often to look while waiting

// Wait for the socket to come back, or give up after RECONNECT_WAIT. The socket
// does not stay down, it keeps flipping open/closed many times a minute, so any
// one call may land in a closed moment. waiting rides over the gap instead of
// turning a few seconds into a failed sync.
async function waitForSocket(): Promise<boolean> {
  if (socketUsable()) return true;
  const until = Date.now() + RECONNECT_WAIT;
  let asked = 0;
  while (Date.now() < until) {
    // ask again every couple of seconds: the first try can land while the old
    // connection is still being torn down.
    if (Date.now() >= asked) {
      reconnect();
      asked = Date.now() + 2000;
    }
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_POLL));
    if (socketUsable()) return true;
  }
  return false;
}

// How long one backend call may stay open. Not a speed budget, it is far longer
// than any real call. it exists so a call that never comes back can not hold a
// slot forever, which is what left the panel stuck on "Loading...". bounds the
// whole guarded operation, not each try.
const CALL_DEADLINE = 60000;

// Biggest argument we put on the wire. Decky closes the socket on a message over
// 4 MB instead of raising, and it does so silently, so we refuse under the cap
// and say which call and how big. Big things go in slices, or named by URL and
// fetched in Python.
const MAX_ARG_CHARS = 3 * 1024 * 1024;

function tooBig(name: string, args: unknown[]): string | undefined {
  // the whole argument list, not each string: aiohttp measures the message, so a
  // big value nested in an object counts just as much. some of these take
  // objects (set_steam_ids, record_shortcut).
  let size: number;
  try {
    size = JSON.stringify(args)?.length ?? 0;
  } catch {
    // not serialisable (a cycle, or a BigInt). Decky can not send it either, so
    // let the call go and fail on its own.
    return undefined;
  }
  if (size <= MAX_ARG_CHARS) return undefined;
  return `${name}: refusing to send ${Math.round(size / 1024)} KB in one `
    + `message (limit ${Math.round(MAX_ARG_CHARS / 1024)} KB) — this would `
    + "close Decky's socket silently. Slice it, or pass a URL and fetch it "
    + "in Python (see docs/RPC-PAYLOAD-LIMITS.md)";
}

// Reject if `work` has not settled in `ms`, so its slot is always freed.
function onADeadline<T>(work: Promise<T>, ms: number, name: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`${name} did not answer in ${ms / 1000}s`)), ms);
    const clear = () => clearTimeout(timer);
    work.then((value) => { clear(); resolve(value); },
              (error) => { clear(); reject(error); });
  });
}

// A backend method, rate-limited and safe over a socket that keeps flipping.
export function guarded<A extends unknown[], R>(
  name: string, background = false,
): (...args: A) => Promise<R> {
  const call = callable<A, R>(name);
  return (...args: A): Promise<R> =>
    withSlot(async () => {
      // checked before the socket: this is the one failure that takes the socket
      // with it. a refusal here costs one call, sending it costs the connection.
      const oversized = tooBig(name, args);
      if (oversized) return Promise.reject(new Error(oversized));
      if (!socketUsable() && !(await waitForSocket())) {
        // thrown, not left pending, and it says what to do: nothing here can
        // reopen the socket, so an error that only says "unreachable" would
        // leave you pressing Sync at something that can never answer.
        return Promise.reject(new Error(
          "Decky's connection to the plugin has been down for "
          + `${RECONNECT_WAIT / 1000}s \u2014 reload Shelfcloud in Decky `
          + `\u203a Settings \u203a Plugins. (${name})`));
      }
      const until = Date.now() + CALL_DEADLINE;
      const left = () => Math.max(1000, until - Date.now());
      try {
        return await onADeadline(call(...args), left(), name);
      } catch (error) {
        // one retry, and only when the call failed WITH the socket. Decky drops
        // the in-flight call when the connection goes, so this lets a sync
        // survive a short gap. if the socket is still up the error is real.
        if (socketUsable()) throw error;
        if (!(await waitForSocket())) throw error;
        return await onADeadline(call(...args), left(), name);
      }
    }, background);
}
