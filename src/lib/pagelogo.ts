// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A logo for a page you added, drawn from its name.
//
// Steam paints the logo slot over the hero and uses it INSTEAD of the title,
// so a tile that has one shows a picture where its name would be. For a
// published page that is fine — the picture IS the name, drawn nicely. For a
// page somebody added themselves it was actively wrong: every one of them got
// the Shelfcloud wordmark, so a shelf of pages all claimed to be called
// Shelfcloud, and the one thing that said which page you were looking at was
// the thing being covered up.
//
// So the name is drawn. The mark on the left says where the tile came from,
// the words say which page it is, and the result is the same shape as every
// publisher logo Steam already draws.
//
// Rendered HERE rather than on the gateway deliberately. The server has
// neither an image library nor a font, and adding both — plus a licence to
// carry — to produce a picture only Steam ever looks at is a large amount of
// machinery for one slot. This runs inside Steam's own UI, which has a canvas
// and a font already, and that font is the one the rest of Steam is set in.
//
// The layout is separated from the drawing on purpose: a canvas cannot be
// exercised in this test suite, but the arithmetic that decides where the
// words go and whether they fit can be, and that is where the bugs are.
//
// The capsule next door is drawn the same way, from the same fitting code —
// see pagecapsule.ts.

import { INK, loadImage, toPng, type Png, type SourceImage } from "./canvas";
import { fitText, tidyName } from "./textfit";

/** Steam's logo canvas.
 *
 *  640 wide like every logo Steam serves. The HEIGHT is not 360, and that is
 *  the point: 360 is what a publisher ships when the artwork FILLS it, and
 *  ours does not. The mark is 150 tall and the words beside it about the
 *  same, so on a 360 canvas the content sat between 29% and 71% of the
 *  height with nothing above or below it.
 *
 *  Steam pins the logo against the bottom of the hero, so that empty 29%
 *  became a gap under the words and the whole thing floated. Disney/Pixar
 *  WALL-E sat visibly higher than the games either side of it.
 *
 *  Measured rather than guessed, off Steam's own CDN. Real logos put their
 *  content between roughly 20% and 85% of the canvas:
 *
 *      Hades           y 19%..87%      Cyberpunk 2077  y 27%..79%
 *      Hollow Knight   y 24%..88%      Terraria        y 25%..78%
 *
 *  and Portal 2 ships 640x191 with the artwork filling all of it, which is
 *  the case that settles it: a short, wide logo canvas is normal and Steam
 *  draws it correctly. So the canvas is cut to the content instead of the
 *  content being floated in a canvas built for someone else's artwork.
 *
 *  At 210 the same content lands at 14%..86%, which is where the measured
 *  logos live. */
export const LOGO_W = 640;
export const LOGO_H = 210;

/** Where the mark sits and how much room is left for words. */
const MARK_BOX = 150;          // the square the mark is drawn into
const GAP = 26;                // between mark and text
const EDGE = 34;               // canvas margin
const LINE = 1.16;             // line height, as a multiple of the type size

/** Type sizes, largest first. The first that fits is used. */
const SIZES = [64, 56, 48, 42, 36, 30, 26, 22];
const MAX_LINES = 2;

export interface LogoLayout {
  /** Point size for the name. */
  size: number;
  /** The name broken into lines, already truncated if it could not fit. */
  lines: string[];
  /** Left edge of the text column. */
  textLeft: number;
  /** Width available to the text. */
  textWidth: number;
  /** Where the mark is drawn. */
  mark: { x: number; y: number; size: number };
}

/**
 * Decide how the name is set.
 *
 * `measure` returns the width of a string at a given size — supplied by the
 * caller so this stays pure. The canvas provides the real one; a test provides
 * a predictable one.
 */
export function logoLayout(
  name: string,
  measure: (text: string, size: number) => number,
): LogoLayout {
  const textLeft = EDGE + MARK_BOX + GAP;
  const textWidth = LOGO_W - textLeft - EDGE;
  const mark = { x: EDGE, y: (LOGO_H - MARK_BOX) / 2, size: MARK_BOX };

  const fitted = fitText(
    tidyName(name),
    { width: textWidth, sizes: SIZES, maxLines: MAX_LINES },
    measure,
  );
  return { size: fitted.size, lines: fitted.lines, textLeft, textWidth, mark };
}

/**
 * Draw the logo and return it as base64 PNG, or undefined if this environment
 * cannot draw.
 *
 * `markPng` is the tile's own square mark, already downloaded for the icon
 * slot — passed in rather than fetched again.
 */
export async function renderPageLogo(
  name: string,
  markPng?: SourceImage,
): Promise<Png | undefined> {
  try {
    const canvas = document.createElement("canvas");
    canvas.width = LOGO_W;
    canvas.height = LOGO_H;
    const context = canvas.getContext("2d");
    if (!context) return undefined;

    const face = '600 {size}px "Motiva Sans", system-ui, sans-serif';
    const measure = (text: string, size: number) => {
      context.font = face.replace("{size}", String(size));
      return context.measureText(text).width;
    };

    const layout = logoLayout(name, measure);

    if (markPng) {
      const mark = await loadImage(`data:image/${markPng.kind};base64,${markPng.data}`);
      if (mark) {
        context.drawImage(mark, layout.mark.x, layout.mark.y,
                          layout.mark.size, layout.mark.size);
      }
    }

    context.font = face.replace("{size}", String(layout.size));
    context.fillStyle = INK;
    context.textBaseline = "middle";
    const lineHeight = layout.size * LINE;
    const block = (layout.lines.length - 1) * lineHeight;
    let y = LOGO_H / 2 - block / 2;
    for (const line of layout.lines) {
      context.fillText(line, layout.textLeft, y);
      y += lineHeight;
    }

    return toPng(canvas);
  } catch {
    // Drawing is a nicety. A tile with no logo shows its name as text, which
    // is the outcome this is trying to produce anyway.
    return undefined;
  }
}
