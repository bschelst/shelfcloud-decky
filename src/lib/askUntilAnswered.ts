// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Ask again a few times when nothing came back. The players badge used to ask
// once, and one failed answer left a dash on the page until you opened it again.
// No @decky/ui in here so it can be tested.

/** Calls `ask` until it gives something back, waiting `waits[i]` ms before
 *  each next try. A throw counts as no answer. Stops early once `gone()` says
 *  the page is closed. Undefined when every try came back empty. */
export async function askUntilAnswered<T>(
  ask: () => Promise<T | undefined>,
  waits: readonly number[],
  gone: () => boolean,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<T | undefined> {
  for (let attempt = 0; attempt <= waits.length; attempt++) {
    if (attempt > 0) await sleep(waits[attempt - 1]);
    if (gone()) return undefined;
    const answer = await ask().catch(() => undefined);
    if (answer !== undefined) return answer;
    if (gone()) return undefined;
  }
  return undefined;
}
