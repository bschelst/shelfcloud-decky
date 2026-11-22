// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// How many people are playing this game on Steam right now. A cloud tile is a
// non-Steam shortcut, so player-count plugins show nothing on those pages. The
// game is usually on Steam though, so we carry the appid on the tile and ask
// the same endpoint directly.

import { type ReactElement } from "react";
import { FaUsers } from "react-icons/fa";

import HeroBadge from "../heroBadge";
import { providerInfo } from "../../lib/theme";
import { useBadgeSettings, usePlayersForApp, useProviderForApp } from "../../lib/badgeData";
import { useParams } from "../../lib/useParams";

/** Thousands separators, in whatever the Deck is set to. */
function pretty(count: number): string {
  try {
    return count.toLocaleString();
  } catch {
    return String(count);
  }
}

export default function PlayersBadge(): ReactElement {
  const { appid } = useParams<{ appid: string }>();
  const provider = useProviderForApp(appid);
  const { players: enabled, playersPosition, loaded } = useBadgeSettings();
  const players = usePlayersForApp(enabled ? appid : undefined);

  // Switched on and one of our tiles. Never drawn on a guessed setting -- see
  // providerBadge: that is how a switched-off badge still flashed up on load.
  if (!loaded || !enabled || !provider) return <></>;

  // Once switched on it always shows: a number when there is one, a dash when
  // not. A dash, not a zero -- no count comes back for a game not on Steam, and
  // "0 playing" would be a claim about a game Steam never heard of.
  const known = players !== undefined;
  // The service's colour, same as the provider badge. They sit on the same hero
  // and belong to the same tile, so a colour of its own looked bolted on.
  const { color: accent } = providerInfo(provider);

  return (
    <HeroBadge position={playersPosition} accent={accent}>
      <FaUsers size={13} />
      <span style={{ color: "#e8ecf2" }}>
        {known ? `${pretty(players)} playing` : "\u2013"}
      </span>
    </HeroBadge>
  );
}
