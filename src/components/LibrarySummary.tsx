// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The panel header: one number, one bar, provider chips. Not a chart -- on a
// ~300px panel a big figure and a bar read better than a pie of two numbers.
// The bar doubles as the live sync progress.

import { plural } from "../lib/summary";
import { theme } from "../lib/theme";
import { KNOWN_PROVIDERS, providerLabel } from "../lib/types";

// The header uses the accent blue whether idle or working. Green when idle used
// to read as a status light for "healthy", which it never reported. Idle and
// working still differ: a deeper blue bar for coverage, a lighter one for live
// progress, and the row below names which of the two it is.
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

  // On a shelf the two numbers are not comparable. `synced` is what this device
  // holds (its shelf plus shared), `available` is every game that could be added
  // anywhere on the account. "6 of 360" would look like a near-empty shelf, so
  // on a shelf we stop comparing them.
  const onShelf = Boolean(shelfName);

  // Shelf count comes from the gateway, the shared half is what is left of what
  // this device holds. Clamped both ways: the two are read at different moments,
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
          fontVariantNumeric: "tabular-nums",   // digits stopp jittering as it counts
        }}>
          {synced}
        </div>
        <div style={{ fontSize: "0.8em", color: DIM, paddingBottom: 2 }}>
          {onShelf
            // Both halves, so the total adds up: 3 on Bart + 3 shared is 6.
            // "6 on Bart" would be false about a shelf the user knows holds 3.
            ? `${onShelfCount} on ${shelfName} + ${sharedCount} shared`
            : available > synced
              ? `of ${available} ${plural(available, "game")}`
              : `${plural(synced, "game")} in your library`}
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
          // fixed height: progress text keeps changing length and a resizing
          // box makes the whole panel jump
          height: "1.2em",
          overflow: "hidden",
          whiteSpace: "nowrap",
          textOverflow: "ellipsis",
        }}>
          {progressLabel}
        </div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, minHeight: "1.2em" }}>
          {Object.entries(byProvider)
            // Fixed order, so a chip keeps its place while the counts tick up
            // during a sync. Object key order shifts as byProvider is rebuilt,
            // which read as the chips jumping around. Known services in their
            // canonical order, anything unknown after them by name.
            .sort(([a], [b]) => {
              const ia = KNOWN_PROVIDERS.indexOf(a);
              const ib = KNOWN_PROVIDERS.indexOf(b);
              return (ia === -1 ? KNOWN_PROVIDERS.length : ia)
                - (ib === -1 ? KNOWN_PROVIDERS.length : ib)
                || a.localeCompare(b);
            })
            .map(([provider, count]) => (
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
