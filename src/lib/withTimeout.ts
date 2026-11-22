// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A promise that is not allowed to take forever. Every Steam client call the sync
// awaits comes from code we can not see. The sync handles a call that FAILS, but
// a promise that simply never settles is not caught by a catch: the await sits
// there and nothing else in the sync runs again (SetCustomArtworkForApp did this
// twice). so every wait gets a deadline, the sync gives up the slot and moves on,
// and a library finishes with a few pictures missing instead of frozen.

// How long one Steam call may take before we give up on it. generous: these write
// a file and talk to the Steam client, and the worst measured case was well under
// a second, so this only fires on a call that is never coming back.
export const STEAM_CALL_TIMEOUT = 15000;

// How long a backend call may take before we give up. longer than a Steam call
// since some do real work (an artwork download, a layout install). still a
// deadline: a message that never arrives would stay pending for the session.
export const RPC_TIMEOUT = 20000;

// How long ONE game may take before the sync drops it and moves on. a backstop,
// not a budget: every call in a game's turn has its own deadline, so reaching this
// means something is hanging what none of those cover. bounding the iteration
// means whatever hangs, the library still finishes and the log names the game.
export const PER_GAME_TIMEOUT = 40000;

// Resolve with `whenLate` if `work` has not settled in `ms`. resolves, not
// rejects: the callers treat a failed slot as "skip it and carry on", so a throw
// would only mean the same catch at every call site. the abandoned promise is
// left alone (no way to cancel it, and nobody is listening if it ever settles).
export function withTimeout<T>(
  work: Promise<T>, whenLate: T, ms = STEAM_CALL_TIMEOUT,
): Promise<T> {
  return new Promise<T>((resolve) => {
    let settled = false;
    const done = (value: T) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => done(whenLate), ms);
    // a rejection is normal here and means the same as a timeout: this slot did
    // not happen. swallowed so the sync sees one shape.
    work.then((value) => done(value), () => done(whenLate));
  });
}

// A Steam call we only need to COMPLETE, whatever it returns. most of these hand
// back something the sync does not read, so this flattens every outcome (value,
// rejection, or never) to the same "it is over now".
export function steamCall(work: unknown, ms = STEAM_CALL_TIMEOUT): Promise<void> {
  return withTimeout(
    Promise.resolve(work).then(() => undefined), undefined, ms,
  );
}
