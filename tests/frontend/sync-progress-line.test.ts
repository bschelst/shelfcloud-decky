// The sync's progress lines go into the job, for the manual and the automatic
// sync both. The automatic one showed only "Syncing…", so a long first sync after
// an update (all art redone) looked hung for minutes.

import { beforeEach, describe, expect, it } from "vitest";

import { getJob, reportSyncLine, resetJob, setJob } from "../../src/lib/job";

beforeEach(() => resetJob());

describe("reportSyncLine", () => {
  it("shows the line and its fraction", () => {
    setJob({ busy: true, message: "Syncing…", progress: null });
    reportSyncLine("Artwork Fortnite (GeForce N… (12/48)");
    expect(getJob().message).toBe("Artwork Fortnite (GeForce N… (12/48)");
    expect(getJob().progress).toBe(0.25);
  });

  it("keeps the last fraction for a line without counter", () => {
    reportSyncLine("Adding X (3/4)");
    reportSyncLine("Collections");
    expect(getJob().message).toBe("Collections");
    expect(getJob().progress).toBe(0.75);
  });

  it("a 0 total does not divide by zero", () => {
    reportSyncLine("Checking X (0/0)");
    expect(getJob().progress).toBeNull();
  });

  it("never goes past the end", () => {
    reportSyncLine("Artwork X (9/8)");
    expect(getJob().progress).toBe(1);
  });
});
