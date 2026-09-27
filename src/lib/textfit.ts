// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Fitting a page's name into a box. Shared by the logo and the capsule so the
// two do not drift apart. Kept pure, with a measuring callback and no canvas:
// overflow is clipped by Steam with no warning, so this is where the bugs are,
// and a pure function can be driven with nasty names in a test with no canvas.

/** How a name may be set: the column to fit, the sizes to try (largest first)
 *  and how many lines are allowed. */
export interface FitOptions {
  width: number;
  sizes: number[];
  maxLines: number;
}

export interface Fitted {
  size: number;       // the largest from `sizes` that fits
  lines: string[];    // cut short if the name could not fit
}

/** Collapse whitespace so a name pasted out of a browser tab sets cleanly. */
export function tidyName(name: string): string {
  return (name ?? "").replace(/\s+/g, " ").trim();
}

/** Choose a size and line breaks for `text`. Empty name yields no lines. */
export function fitText(
  text: string,
  options: FitOptions,
  measure: (text: string, size: number) => number,
): Fitted {
  const smallest = options.sizes[options.sizes.length - 1];
  const clean = tidyName(text);
  if (!clean) return { size: smallest, lines: [] };

  for (const size of options.sizes) {
    const lines = wrap(clean, options.width, size, options.maxLines, measure);
    if (lines) return { size, lines };
  }

  // nothing fit even at the smallest size — one long unbroken word, or a script
  // this font sets wide. cut it rather than overflow, Steam gives no room to spill.
  return { size: smallest, lines: [ellipsise(clean, options.width, smallest, measure)] };
}

/** Break `text` into at most `maxLines` that each fit, or null if impossible. */
function wrap(
  text: string,
  width: number,
  size: number,
  maxLines: number,
  measure: (text: string, size: number) => number,
): string[] | null {
  if (measure(text, size) <= width) return [text];

  const words = text.split(" ");
  if (words.length < 2) return null;            // one word, cannot be broken

  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (measure(candidate, size) <= width) {
      current = candidate;
      continue;
    }
    if (current) lines.push(current);
    // A single word wider than the column cannot be placed at this size.
    if (measure(word, size) > width) return null;
    current = word;
    if (lines.length >= maxLines) return null;
  }
  if (current) lines.push(current);
  return lines.length <= maxLines ? lines : null;
}

/** Cut `text` down until it fits, with an ellipsis. */
export function ellipsise(
  text: string,
  width: number,
  size: number,
  measure: (text: string, size: number) => number,
): string {
  if (measure(text, size) <= width) return text;
  let cut = text;
  while (cut.length > 1 && measure(`${cut}…`, size) > width) {
    cut = cut.slice(0, -1);
  }
  return `${cut.trimEnd()}…`;
}
