// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// One place for colour. Steam's Game Mode is a dark blue-grey, so the accent is
// a cloud blue from the same family as Steam's own selection colour, and the
// neutrals lean blue rather than pure grey, to look native.

import { providerLabel } from "./types";

export const theme = {
  /** Shelfcloud's own colour. Used for focus, headings and the active state. */
  accent: "#4d9fff",
  accentSoft: "rgba(77, 159, 255, 0.12)",
  accentText: "#8ec5ff",

  /** Panel surfaces, lifted slightly off Steam's background. */
  surface: "rgba(255, 255, 255, 0.04)",
  surfaceRaised: "rgba(255, 255, 255, 0.07)",
  border: "rgba(255, 255, 255, 0.10)",

  text: "#e8eaed",
  textDim: "rgba(232, 234, 237, 0.72)",
  textFaint: "rgba(232, 234, 237, 0.48)",

  /** Status colours, kept apart from the accent: a green that also means
   *  "GeForce NOW" would mix up state and branding. */
  good: "#5ac878",
  warn: "#f0b429",
  bad: "#ff6b6b",

  /** A focus tint made from whatever colour a section carries, so the highlight
   *  belongs to that section. */
  tint(color: string, alpha: number): string {
    const hex = color.replace("#", "");
    if (hex.length !== 6) return `rgba(255, 255, 255, ${alpha})`;
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  },
} as const;

/** Provider colours, from each service's own branding. Identity, not decoration,
 *  so they never double as status colours. */
// Colours only. The label comes from types.ts, the one place a provider is
// named -- two copys is what shipped "Fortnite (amazon_luna)" when Luna landed.
const PROVIDER_COLORS: Record<string, string> = {
  boosteroid: "#7b61ff",
  geforce_now: "#76b900",
  xbox_cloud: "#107c10",
  // Luna's purple, far enough from Boosteroid's to tell them apart at a glance.
  amazon_luna: "#c86dd7",
};

export function providerInfo(key: string) {
  return { label: providerLabel(key), color: PROVIDER_COLORS[key] ?? theme.textDim };
}
