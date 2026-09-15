// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// One place for colour, so the page reads as designed rather than assembled.
//
// Steam's Game Mode is a dark, desaturated blue-grey. Picking an accent that
// sits *with* that rather than fighting it is the difference between a plugin
// page that looks native and one that looks bolted on — so the accent is a
// cloud blue drawn from the same family as Steam's own selection colour, and
// the neutrals are blue-biased rather than pure grey.

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

  /** Semantic, and deliberately separate from the accent: a green that also
   *  means "GeForce NOW" would make state and branding indistinguishable. */
  good: "#5ac878",
  warn: "#f0b429",
  bad: "#ff6b6b",

  /** A focus tint derived from whatever colour a section carries, so the
   *  highlight always belongs to that section rather than to a fixed accent. */
  tint(color: string, alpha: number): string {
    const hex = color.replace("#", "");
    if (hex.length !== 6) return `rgba(255, 255, 255, ${alpha})`;
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  },
} as const;

/** Provider colours, taken from each service's own branding.
 *
 * These are identity, not decoration — a row tinted GeForce green should mean
 * GeForce and nothing else, which is why they never double as status colours.
 */
// Colours ONLY. The label comes from types.ts, which is the one place a
// provider is named — this file used to carry its own copy, and keeping two in
// step is exactly what failed when Luna was added: the guide here knew the
// name, and the Steam tile title did not, so it shipped as
// "Fortnite (amazon_luna)".
const PROVIDER_COLORS: Record<string, string> = {
  boosteroid: "#7b61ff",
  geforce_now: "#76b900",
  xbox_cloud: "#107c10",
  // Luna's own purple. Far enough from Boosteroid's that a tinted row still
  // names one service at a glance, which is the only thing these are for.
  amazon_luna: "#c86dd7",
};

export function providerInfo(key: string) {
  return { label: providerLabel(key), color: PROVIDER_COLORS[key] ?? theme.textDim };
}
