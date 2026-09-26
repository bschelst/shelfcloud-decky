// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A promise that is not allowed to take forever.
//
// Every Steam client call the sync awaits is a promise handed back by code
// none of this can see. The sync already assumes such a call may FAIL -- each
// artwork slot is wrapped so one bad picture does not cost the others -- but
// it assumed a call always SETTLES, and that is a different thing. A promise
// that simply never resolves is not caught by a catch; the await sits there,
// the loop never comes round, and nothing else in the sync ever runs again.
//
// That happened, twice, on the same call. SetCustomArtworkForApp stopped
// coming back partway through a library: no error, no CPU, no network, no
// further grid files -- and because the sync is serial, all 800-odd games
// behind it stopped too. The panel showed a Cancel button that could do
// nothing, because the loop that reads the cancel flag was never reached.
//
// So every wait gets a deadline. The slot is given up on, the sync moves to
// the next game, and a library finishes with a few pictures missing instead
// of stopping dead with most of them missing. A missing capsule is a retry
// next sync; a frozen sync is a plugin that needs restarting.

/** How long any single Steam call may take before it is abandoned.
 *
 *  Generous on purpose. These calls write a file and talk to the Steam client,
 *  and on a busy Deck mid-sync that is not instant -- the measured worst case
 *  through the gateway was well under a second, so seconds of headroom means
 *  this only ever fires on a call that is genuinely never coming back. */
export const STEAM_CALL_TIMEOUT = 15000;

/** How long a call into the Python backend may take before it is abandoned.
 *
 *  Longer than a Steam call, because some of these genuinely do work -- an
 *  artwork download fetches over the network, and a layout install writes
 *  files. Still a deadline: these are promises over Decky's socket, and a
 *  message that never arrives leaves one pending for the life of the session.
 *  The backend sat idle through two frozen syncs for exactly this reason --
 *  nothing was wrong with it, the frontend had simply stopped asking. */
export const RPC_TIMEOUT = 45000;

/** Resolve with `whenLate` if `work` has not settled in `ms`.
 *
 *  Resolves rather than rejects: the callers here treat a failed slot as "skip
 *  it and carry on", and making a timeout throw would only mean writing the
 *  same catch at every call site. The abandoned promise is left alone -- there
 *  is no way to cancel it, and if it ever does settle nobody is listening.
 */
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
    // A rejection is a normal outcome here and means the same as a timeout:
    // this slot did not happen. Swallowed so the sync sees one shape.
    work.then((value) => done(value), () => done(whenLate));
  });
}

/** A Steam call we only need to COMPLETE, whatever it hands back.
 *
 *  Most of these return something the sync does not read -- it cares that the
 *  artwork was written, not what the client said about it -- so this flattens
 *  every outcome (value, rejection, or never) to the same "it is over now".
 */
export function steamCall(work: unknown, ms = STEAM_CALL_TIMEOUT): Promise<void> {
  return withTimeout(
    Promise.resolve(work).then(() => undefined), undefined, ms,
  );
}
