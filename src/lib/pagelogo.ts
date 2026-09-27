// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A logo for an added page, drawn from its name. Steam paints the logo slot
// over the hero INSTEAD of the title, so every added page used to show the
// Shelfcloud wordmark and none said which page it was. So we draw the name.
//
// Drawn here, not on the gateway: the server has no image library or font, and
// this runs inside Steam's UI, which has a canvas and the right font already.
// The layout is kept apart from the drawing so the fitting math can be tested
// without a canvas. The capsule next door works the same way, see
// pagecapsule.ts.

import { INK, loadImage, toPng, type Png, type SourceImage } from "./canvas";
import { fitText, tidyName } from "./textfit";

/** Steam's logo canvas. 640 wide like every logo Steam serves. The height is
 *  NOT 360: our content only fills the middle, so on a 360 canvas Steam pinned
 *  it to the hero bottom and a gap floated above. Real logos, measured off
 *  Steam's CDN, keep their content between about 20% and 85%; at 210 ours lands
 *  at 14%..86%, right where those sit. */
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
  size: number;
  lines: string[];    // cut short if the name could not fit
  textLeft: number;
  textWidth: number;  // width the widest line takes at `size`
  mark: { x: number; y: number; size: number };
}

/** Decide how the name is set. `measure` is passed in so this stays pure — the
 *  canvas gives the real one, a test a fixed one. */
export function logoLayout(
  name: string,
  measure: (text: string, size: number) => number,
): LogoLayout {
  // Widest the text column can be, with the mark pinned to the left margin.
  const maxTextWidth = LOGO_W - (EDGE + MARK_BOX + GAP) - EDGE;

  const fitted = fitText(
    tidyName(name),
    { width: maxTextWidth, sizes: SIZES, maxLines: MAX_LINES },
    measure,
  );

  // The name rarely fills the column, so pinned left the group sat off to one
  // side while Steam's provider chip stays centred under the slot. Centre the
  // whole group so the mark, the name and the chip line up.
  const textWidth = fitted.lines.reduce(
    (widest, line) => Math.max(widest, measure(line, fitted.size)), 0);
  const contentWidth = textWidth > 0 ? MARK_BOX + GAP + textWidth : MARK_BOX;
  const groupLeft = Math.max(EDGE, (LOGO_W - contentWidth) / 2);

  const mark = { x: groupLeft, y: (LOGO_H - MARK_BOX) / 2, size: MARK_BOX };
  const textLeft = groupLeft + MARK_BOX + GAP;
  return { size: fitted.size, lines: fitted.lines, textLeft, textWidth, mark };
}

/** Draw the logo as base64 PNG, or undefined if this environment cannot draw.
 *  `markPng` is the tile's own square mark, already downloaded for the icon. */
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
    // drawing is a nicety. with no logo the tile just shows its name as text.
    return undefined;
  }
}
