// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The panel's header: one number, one bar, provider chips.
//
// Deliberately not a chart. A chart earns its place when there is a trend or a
// distribution to read; "184 games" is a single number, and a pie of 184-vs-124
// says less than the numbers themselves. On a ~300px panel held at arm's length
// the legible thing is a big figure and a bar.
//
// The same bar doubles as the live sync progress, so the most prominent element
// always answers "where am I?" — idle or working.

import { theme } from "../lib/theme";
import { providerLabel } from "../lib/types";

// The header uses Shelfcloud's accent throughout, idle and working alike.
//
// It used to be green when idle and blue only while syncing, which read as a
// status light — green for "healthy" — when it was never reporting health at
// all: the number is a count and the bar is coverage. A colour implying a
// state that does not exist is worse than no colour, and it made the header
// the one element on the panel not speaking the plugin's own blue.
//
// Idle and working stay distinguishable without it: the bar keeps a deeper
// blue for coverage and a lighter one for live progress, and the row beneath
// swaps the provider chips for the progress line, which says outright which
// of the two the bar is showing.
const DIM = "rgba(255,255,255,0.55)";
const BAR_IDLE = `linear-gradient(90deg, ${theme.accent}, ${theme.accentText})`;
const BAR_WORKING = `linear-gradient(90deg, ${theme.accentText}, #bfe0ff)`;

export interface LibrarySummaryProps {
  synced: number;
  /** Total playable games available to add (owned + free). */
  available: number;
  /** The shelf this device shows, when it is on one. See below. */
  shelfName?: string | null;
  /** How many games sit on that shelf itself, excluding the shared ones. */
  shelfGames?: number | null;
  byProvider: Record<string, number>;
  /** When syncing, 0..1 — the bar shows work done instead of coverage. */
  progress?: number | null;
  progressLabel?: string;
}

export function LibrarySummary({
  synced, available, shelfName, shelfGames, byProvider, progress, progressLabel,
}: LibrarySummaryProps) {
  const working = typeof progress === "number";

  // On a shelf the two numbers stop being comparable, so they stop being
  // compared. `synced` is what this device holds — its shelf plus the shared
  // games — while `available` counts every owned game that could be added
  // ANYWHERE on the account. Shown together as "6 of 360" they read as "the
  // shelf is ignoring 354 games", and a coverage bar drawn from 6/360 makes a
  // full shelf look 2% full.
  const onShelf = Boolean(shelfName);

  // The shelf's own count comes from the gateway; the shared half is whatever
  // is left of what this device holds. Clamped both ways because the two are
  // read at different moments — mid-sync `synced` can trail the shelf count,
  // and a negative "shared" would be worse than a stale one.
  const onShelfCount = Math.max(0, Math.min(synced, shelfGames ?? 0));
  const sharedCount = Math.max(0, synced - onShelfCount);

  // Guard against a zero denominator before the first Steam-library sync.
  const coverage = available > 0 ? Math.min(1, synced / available) : 0;
  const fill = working ? Math.max(0, Math.min(1, progress!))
    : (onShelf ? 1 : coverage);

  return (
    <div style={{ padding: "4px 0 2px" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <div style={{
          fontSize: "2.1em",
          fontWeight: 700,
          lineHeight: 1,
          color: theme.accent,
          fontVariantNumeric: "tabular-nums",   // digits stop jittering as it counts
        }}>
          {synced}
        </div>
        <div style={{ fontSize: "0.8em", color: DIM, paddingBottom: 2 }}>
          {onShelf
            // Both halves, so the total adds up in front of the reader. A
            // device on a shelf holds that shelf's games AND the shared ones,
            // so 3 on Bart with 3 shared is 6 — and "6 on Bart" states
            // something false about a shelf the user knows holds three.
            ? `${onShelfCount} on ${shelfName} + ${sharedCount} shared`
            : available > synced ? `of ${available} games` : "games in your library"}
        </div>
      </div>

      <div style={{
        height: 6,
        borderRadius: 999,
        background: "rgba(255,255,255,0.12)",
        overflow: "hidden",
        margin: "9px 0 7px",
      }}>
        <div style={{
          width: `${fill * 100}%`,
          height: "100%",
          borderRadius: 999,
          background: working ? BAR_WORKING : BAR_IDLE,
          transition: "width 220ms ease",
        }} />
      </div>

      {working ? (
        <div style={{
          fontSize: "0.78em",
          color: DIM,
          // Fixed height: progress text changes length constantly and a box
          // that resizes makes the whole panel jump.
          height: "1.2em",
          overflow: "hidden",
          whiteSpace: "nowrap",
          textOverflow: "ellipsis",
        }}>
          {progressLabel}
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: "1.2em" }}>
          {Object.entries(byProvider).map(([provider, count]) => (
            <span
              key={provider}
              style={{
                fontSize: "0.7em",
                padding: "2px 7px",
                borderRadius: 999,
                background: theme.tint(theme.accent, 0.16),
                border: `1px solid ${theme.tint(theme.accent, 0.35)}`,
                color: theme.accentText,
                whiteSpace: "nowrap",
              }}
            >
              {providerLabel(provider)} {count}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
