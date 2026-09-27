// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A job that starts must end, and a job that is running must be stoppable.
//
// Every panel button is disabled while `busy`, so `busy` stuck true bricks the
// panel. "Remove all games" once missed clearing it on its success path.
// runJob guarantees one entry, one exit. Cancel is the other half: "Add all my
// games" had no way to stop short of killing Steam.

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
    // the bug's exact shape: work done, nothing reported, panel not put back.
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
    // else a stale press would arm cancel for whatever runs next.
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
    // what shipped: doSync set busy without runJob, so cancel was never cleared
    // and stopped every later sync at its first tile while reporting success.
    setJob({ busy: true });
    cancelJob();
    expect(cancelRequested()).toBe(true);
    setJob({ busy: false });

    setJob({ busy: true });               // the next sync, started the old way
    expect(cancelRequested()).toBe(false);
  });

  it("does not re-arm on a message sent mid-run", () => {
    // only the move into busy clears cancel; a progress line must not undo it.
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
    // the body never settles, so nothing clears `busy`. The store is module
    // scope, so closing the panel does not help. A layout-blocked sync did this.
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
    // honouring Cancel must not be undone by a later deadline clearing a new run.
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
