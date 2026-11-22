import { describe, expect, it } from "vitest";

import { askUntilAnswered } from "../../src/lib/askUntilAnswered";

const noWait = async () => undefined;

describe("askUntilAnswered", () => {
  it("returns the first answer when it is good", async () => {
    let calls = 0;
    const got = await askUntilAnswered(async () => { calls++; return 5; }, [10, 20], () => false, noWait);
    expect(got).toBe(5);
    expect(calls).toBe(1);
  });

  it("asks again after nothing came back", async () => {
    const answers = [undefined, undefined, 42];
    let calls = 0;
    const got = await askUntilAnswered(async () => answers[calls++], [10, 20], () => false, noWait);
    expect(got).toBe(42);
    expect(calls).toBe(3);
  });

  it("treats a thrown call like no answer", async () => {
    let calls = 0;
    const got = await askUntilAnswered(async () => {
      calls++;
      if (calls === 1) throw new Error("did not answer in 15s");
      return 7;
    }, [10], () => false, noWait);
    expect(got).toBe(7);
  });

  it("gives up after the last wait", async () => {
    let calls = 0;
    const got = await askUntilAnswered(async () => { calls++; return undefined; }, [10, 20], () => false, noWait);
    expect(got).toBeUndefined();
    expect(calls).toBe(3);
  });

  it("stops as soon as the page is gone", async () => {
    let calls = 0;
    let gone = false;
    const got = await askUntilAnswered(async () => { calls++; gone = true; return undefined; },
      [10, 20], () => gone, noWait);
    expect(got).toBeUndefined();
    expect(calls).toBe(1);
  });

  it("waits the given times between asks", async () => {
    const waited: number[] = [];
    await askUntilAnswered(async () => undefined, [3000, 15000], () => false,
      async (ms) => { waited.push(ms); });
    expect(waited).toEqual([3000, 15000]);
  });
});
