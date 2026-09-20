// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The two bits of browser that drawing a tile needs.
//
// Separated from the tiles themselves so the layout modules stay pure and
// testable, and so the capsule does not have to reach into the logo to borrow
// them.

/** The palette the shipped tiles are drawn in.
 *
 *  Both sampled out of the PNGs rather than copied from the SVG, and that is
 *  the point: the SVG's #0f1621 is the colour of the shelf slots CUT OUT of
 *  the cloud, not the ground behind it. Using it here would have put a drawn
 *  capsule on a visibly lighter navy than the header and hero the gateway
 *  serves for the same tile. The three opaque tiles are all #0b0f16, and the
 *  wordmark is set in #e8eaed. */
export const GROUND = "#0b0f16";
export const INK = "#e8eaed";

export interface Png {
  data: string;
  kind: "png";
}

/** An image already downloaded for another slot, ready to draw. */
export interface SourceImage {
  data: string;
  kind: "png" | "jpg";
}

export function loadImage(src: string): Promise<HTMLImageElement | null> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
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
