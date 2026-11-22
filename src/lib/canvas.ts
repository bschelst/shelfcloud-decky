// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The two bits of browser that drawing a tile needs. Kept apart from the tiles
// so the layout modules stay pure and testable.

/** The palette the shipped tiles are drawn in. Sampled from the PNGs, not the
 *  SVG: the SVG's #0f1621 is the slots cut out of the cloud, and using it gave
 *  a lighter navy. */
export const GROUND = "#0b0f16";
export const INK = "#e8eaed";

export interface Png {
  data: string;
  kind: "png";
}

/** An image already downloaded for an other slot, ready to draw. */
export interface SourceImage {
  data: string;
  kind: "png" | "jpg";
}

/** How long an image may take to decode before it is given up on. */
const DECODE_TIMEOUT = 10000;

export function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    // onload/onerror cover an image that arrives or is refused, not a decode
    // that never finishes — so a timeout, else one bad image stops the sync.
    let done = false;
    const finish = (value: HTMLImageElement | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), DECODE_TIMEOUT);
    const image = new Image();
    image.onload = () => finish(image);
    image.onerror = () => finish(null);
    image.src = src;
  });
}

/** Read a canvas back as base64 PNG, or undefined if it will not give one. */
export function toPng(canvas: HTMLCanvasElement): Png | undefined {
  const url = canvas.toDataURL("image/png");
  const comma = url.indexOf(",");
  if (comma < 0) return undefined;
  return { data: url.slice(comma + 1), kind: "png" };
}
