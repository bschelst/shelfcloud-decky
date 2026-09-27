// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The job store makes progress survive closing the panel. "Add all my games"
// kept its progress in React state inside the panel, so closing Quick Access
// unmounted it and the state died while the work carried on. The tests model
// that: subscribe (open), unsubscribe (close), subscribe again (reopen), and
// check the reopened panel still sees the work in flight.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { finishJob, getJob, resetJob, setJob, subscribeJob } from "../../src/lib/job";

beforeEach(() => resetJob());

describe("surviving the panel closing", () => {
  it("still reports a running job after unsubscribe and resubscribe", () => {
    // panel open, user press "Add all my games".
    const openPanel = vi.fn();
    const closePanel = subscribeJob(openPanel);
    setJob({ busy: true, message: "Reading your Steam library…" });
    expect(getJob().busy).toBe(true);

    // panel closed. the work keeps going and keeps reporting
    closePanel();
    setJob({ message: "Adding Fortnite (12/124)", progress: 12 / 124 });

    // panel reopened: it must see the run in progress, not a clean slate.
    subscribeJob(vi.fn());
    const seen = getJob();
    expect(seen.busy).toBe(true);
    expect(seen.message).toBe("Adding Fortnite (12/124)");
    expect(seen.progress).toBeCloseTo(0.0968, 3);
  });

  it("a closed panel receives no updates but misses no state", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeJob(listener);
    setJob({ busy: true });
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    setJob({ message: "still working" });
    expect(listener).toHaveBeenCalledTimes(1);   // not notified
    expect(getJob().message).toBe("still working");   // but not lost
  });

  it("starts idle", () => {
    expect(getJob()).toEqual({ busy: false, message: "", progress: null, completed: 0 });
  });
});

describe("snapshot identity", () => {
  it("returns a new object on every change", () => {
    // useSyncExternalStore compares snapshots by identity; mutating in place
    // would leave a mounted panel showing stale text.
    const before = getJob();
    setJob({ message: "changed" });
    expect(getJob()).not.toBe(before);
  });

  it("returns a stable object between changes", () => {
    // a snapshot wich differs on every read makes React re-render in a loop.
    setJob({ message: "settled" });
    expect(getJob()).toBe(getJob());
  });

  it("leaves untouched fields alone", () => {
    setJob({ busy: true, message: "one", progress: 0.5 });
    setJob({ message: "two" });
    expect(getJob()).toMatchObject({ busy: true, message: "two", progress: 0.5 });
  });
});

describe("notifying subscribers", () => {
  it("wakes every subscriber", () => {
    const a = vi.fn();
    const b = vi.fn();
    subscribeJob(a);
    subscribeJob(b);
    setJob({ busy: true });
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("unsubscribing one leaves the other subscribed", () => {
    const staying = vi.fn();
    const leaving = vi.fn();
    subscribeJob(staying);
    subscribeJob(leaving)();
    setJob({ busy: true });
    expect(staying).toHaveBeenCalledTimes(1);
    expect(leaving).not.toHaveBeenCalled();
  });
});

describe("finishing", () => {
  it("clears busy and progress, and bumps the completion counter", () => {
    setJob({ busy: true, progress: 0.4 });
    finishJob("124 games — 12 added");
    expect(getJob()).toMatchObject({
      busy: false,
      progress: null,
      completed: 1,
      message: "124 games — 12 added",
    });
  });

  it("keeps the current message when given none", () => {
    setJob({ busy: true, message: "half way" });
    finishJob();
    expect(getJob().message).toBe("half way");
    expect(getJob().busy).toBe(false);
  });

  it("counts each completion, so a reopened panel knows to refresh", () => {
    finishJob();
    finishJob();
    finishJob();
    expect(getJob().completed).toBe(3);
  });

  it("notifies subscribers", () => {
    const listener = vi.fn();
    subscribeJob(listener);
    finishJob("done");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
