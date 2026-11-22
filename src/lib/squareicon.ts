// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A square icon cut out of the game's capsule, for games the gateway has no
// icon for (most of the catalogue). The fallback was the LOGO, a wide strip of
// white text on nothing, and Steam draws an icon small and square on black, so
// every one of those tiles showed a black blob with some letters in it. The
// middle of the capsule is the game's own art, and it is square enough there.

import { loadImage, toPng, type Png, type SourceImage } from "./canvas";

/** Steam's icon size. Bigger buys nothing, the icon is drawn small. */
export const ICON_SIZE = 256;

/** The centred square of a w x h picture, or undefined for a nonsense size. */
export function squareCrop(w: number, h: number): { sx: number; sy: number; size: number } | undefined {
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return undefined;
  const size = Math.min(w, h);
  return { sx: Math.round((w - size) / 2), sy: Math.round((h - size) / 2), size };
}

/** The square icon, or undefined when the picture will not load or draw. Runs
 *  inside Steam's UI, which has a canvas. Never throws: an icon is never worth
 *  failing the sync over. */
export async function renderSquareIcon(source: SourceImage): Promise<Png | undefined> {
  try {
    const image = await loadImage(`data:image/${source.kind};base64,${source.data}`);
    if (!image) return undefined;
    const crop = squareCrop(image.naturalWidth, image.naturalHeight);
    if (!crop) return undefined;
    const canvas = document.createElement("canvas");
    canvas.width = ICON_SIZE;
    canvas.height = ICON_SIZE;
    const context = canvas.getContext("2d");
    if (!context) return undefined;
    context.imageSmoothingQuality = "high";
    context.drawImage(image, crop.sx, crop.sy, crop.size, crop.size, 0, 0, ICON_SIZE, ICON_SIZE);
    return toPng(canvas);
  } catch {
    return undefined;
  }
}
