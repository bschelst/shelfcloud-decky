// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A job that starts must end, and a job that is running must be stoppable.
//
// Both of these are about the same field. Every button in the panel is
// disabled while `busy`, so `busy` left true is not a cosmetic bug: the panel
// is bricked for the rest of the session with nothing running behind it.
//
// That happened. "Remove all games" set busy itself and cleared it on three of
// its four exits, and the one it missed was the path where everything worked —
// so emptying the library, the case that succeeds, greyed out Sync and Remove
// and left no way back but reloading the plugin. runJob exists so that shape
// cannot come back: one entry, one exit, whatever the body does.
//
// The cancel half is the other end of the same story. "Add all my games" walks
// a whole Steam library and there was no way to stop it short of killing Steam.

import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelJob, cancelRequested, finishJob, getJob, resetJob, runJob, setJob,
} from "../../src/lib/job";

beforeEach(() => resetJob());

describe("a job always ends", () => {
  it("clears busy when the body returns normally", async () => {
    await runJob(async () => "Removed 3 tiles.");
    expect(getJob().busy).toBe(false);
    expect(getJob().message).toBe("Removed 3 tiles.");
  });

  it("clears busy when the body returns nothing at all", async () => {
    // The exact shape of the bug: work done, nothing reported, and previously
    // nothing to put the panel back.
    await runJob(async () => { /* worked, said nothing */ });
    expect(getJob().busy).toBe(false);
  });

  it("clears busy when the body throws", async () => {
    await runJob(async () => { throw new Error("gateway said no"); });
    const seen = getJob();
    expect(seen.busy).toBe(false);
    expect(seen.message).toContain("gateway said no");
  });

  it("clears busy when the body returns early", async () => {
    await runJob(async () => {
      if (Date.now() >= 0) return "could not clear your library";
      return "unreachable";
    });
    expect(getJob().busy).toBe(false);
  });

  it("bumps completed so an open panel re-reads its counts", async () => {
    const before = getJob().completed;
    await runJob(async () => undefined);
    expect(getJob().completed).toBe(before + 1);
  });

  it("is busy WHILE the body runs, not only afterwards", async () => {
    let seenDuring: boolean | null = null;
    await runJob(async () => {
      seenDuring = getJob().busy;
      return undefined;
    });
    expect(seenDuring).toBe(true);
    expect(getJob().busy).toBe(false);
  });
});

describe("cancelling", () => {
  it("is not requested before anyone asks", () => {
    expect(cancelRequested()).toBe(false);
  });

  it("asks the running job to stop", async () => {
    const seen: boolean[] = [];
    await runJob(async () => {
      seen.push(cancelRequested());     // before
      cancelJob();
      seen.push(cancelRequested());     // after
      return undefined;
    });
    expect(seen).toEqual([false, true]);
  });

  it("says so in the message, so the button is not the only feedback", async () => {
    await runJob(async () => {
      cancelJob();
      expect(getJob().message).toBe("Stopping…");
      return "Stopped";
    });
  });

  it("does nothing when nothing is running", () => {
    // Otherwise a stale press would arm cancellation for whatever ran NEXT,
    // stopping a run the user never asked to stop.
    cancelJob();
    expect(cancelRequested()).toBe(false);
    expect(getJob().busy).toBe(false);
  });

  it("is cleared before the next job, not left armed", async () => {
    await runJob(async () => { cancelJob(); return undefined; });
    expect(cancelRequested()).toBe(false);

    const during: boolean[] = [];
    await runJob(async () => { during.push(cancelRequested()); return undefined; });
    expect(during).toEqual([false]);
  });

  it("leaves the panel idle after a cancelled run like any other", async () => {
    await runJob(async () => { cancelJob(); return "Stopped"; });
    expect(getJob().busy).toBe(false);
  });

  it("does not leak into a run started WITHOUT runJob", () => {
    // The shape that shipped. doSync and doAddOwned set busy themselves rather
    // than going through runJob, so the flag was never cleared for them: one
    // press of Cancel stopped every later sync at its first tile, for the life
    // of the session, while the panel reported success each time. Observed as
    // a library stuck at 15 tiles against 387 games, syncing "fine".
    setJob({ busy: true });
    cancelJob();
    expect(cancelRequested()).toBe(true);
    setJob({ busy: false });

    setJob({ busy: true });               // the next sync, started the old way
    expect(cancelRequested()).toBe(false);
  });

  it("does not re-arm on a message sent mid-run", () => {
    // Only the transition into busy clears it. A progress line arriving after
    // Cancel must not undo the cancel.
    setJob({ busy: true });
    cancelJob();
    setJob({ message: "Adding Fortnite (12/124)" });
    expect(cancelRequested()).toBe(true);
  });
});

describe("the panel keeps seeing a run it did not start", () => {
  it("reports busy to a subscriber that arrives mid-job", async () => {
    const woken = vi.fn();
    let sawBusy: boolean | null = null;
    await runJob(async () => {
      setJob({ message: "Adding Fortnite (12/124)" });
      woken();
      sawBusy = getJob().busy;
      return undefined;
    });
    expect(sawBusy).toBe(true);
    expect(woken).toHaveBeenCalled();
  });
});


describe("a run that stops responding", () => {
  it("still gives the panel back when Cancel is not honoured", () => {
    // The failure this exists for: the body never settles, so neither its
    // success nor its failure path runs, so nothing ever clears `busy`. The
    // store is module scope, so closing the panel does not help -- every
    // button stays disabled for the rest of the Steam session with nothing
    // running to cancel. A sync blocked writing thousands of controller
    // layouts did exactly this.
    vi.useFakeTimers();
    try {
      setJob({ busy: true, message: "Syncing…" });
      void new Promise<void>(() => {});     // a body that never settles

      cancelJob();
      expect(cancelRequested()).toBe(true);
      expect(getJob().busy).toBe(true);     // asked politely, still waiting

      vi.advanceTimersByTime(9000);
      expect(getJob().busy).toBe(true);     // the deadline has not passed yet

      vi.advanceTimersByTime(2000);
      expect(getJob().busy).toBe(false);    // taken back regardless
      expect(getJob().message).toMatch(/stopped responding/i);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not reset a job that stopped on its own in time", () => {
    // The other half: honouring Cancel must not then be undone by a deadline
    // firing later and clearing a DIFFERENT run that has since started.
    vi.useFakeTimers();
    try {
      setJob({ busy: true, message: "Syncing…" });
      cancelJob();
      finishJob("Stopped.");               // the run noticed, as it should
      expect(getJob().busy).toBe(false);

      setJob({ busy: true, message: "Second run" });
      vi.advanceTimersByTime(30000);
      expect(getJob().busy).toBe(true);    // untouched by the old deadline
    } finally {
      vi.useRealTimers();
    }
  });
});
