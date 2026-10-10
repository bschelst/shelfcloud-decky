import { describe, expect, it } from "vitest";
import { createSyncGate, HOLD_MAX_MS } from "../../src/lib/syncGate";

describe("sync gate", () => {
  it("lets a sync through while the panel is closed", () => {
    const gate = createSyncGate(() => 0);
    expect(gate.hold()).toBe(false);
    expect(gate.close()).toBe(false);
  });

  it("holds the sync while open and asks for one when it closes", () => {
    const gate = createSyncGate(() => 0);
    gate.open();
    expect(gate.hold()).toBe(true);
    expect(gate.hold()).toBe(true);
    expect(gate.close()).toBe(true);
    expect(gate.hold()).toBe(false);
  });

  it("does not ask for a sync when none was held back", () => {
    const gate = createSyncGate(() => 0);
    gate.open();
    expect(gate.close()).toBe(false);
  });

  it("forgets the held sync after it has run once", () => {
    const gate = createSyncGate(() => 0);
    gate.open();
    gate.hold();
    expect(gate.close()).toBe(true);
    expect(gate.close()).toBe(false);
  });

  it("stops holding when a close was never reported", () => {
    let t = 0;
    const gate = createSyncGate(() => t);
    gate.open();
    t = HOLD_MAX_MS - 1;
    expect(gate.hold()).toBe(true);
    t = HOLD_MAX_MS;
    expect(gate.hold()).toBe(false);
  });

  it("starts the clock again on every open", () => {
    let t = 0;
    const gate = createSyncGate(() => t);
    gate.open();
    t = HOLD_MAX_MS + 5;
    gate.close();
    gate.open();
    expect(gate.hold()).toBe(true);
  });
});
