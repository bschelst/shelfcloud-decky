// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// One place for colour, so the page reads as designed rather than assembled.
//
// Steam's Game Mode is a dark, desaturated blue-grey. Picking an accent that
// sits *with* that rather than fighting it is the difference between a plugin
// page that looks native and one that looks bolted on — so the accent is a
// cloud blue drawn from the same family as Steam's own selection colour, and
// the neutrals are blue-biased rather than pure grey.

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
export const providerTheme: Record<string, { label: string; color: string }> = {
  boosteroid: { label: "Boosteroid", color: "#7b61ff" },
  geforce_now: { label: "GeForce NOW", color: "#76b900" },
  xbox_cloud: { label: "Xbox Cloud", color: "#107c10" },
};

export function providerInfo(key: string) {
  return providerTheme[key] ?? { label: key, color: theme.textDim };
}
