// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// How many people are playing this game on Steam, right now.
//
// The PlayCount plugin already does this for Steam games and cannot do it for
// ours: a cloud tile is a NON-Steam shortcut, so the client has no appid to
// ask about and every player-count plugin shows nothing on those pages. The
// game itself is usually on Steam and the number is public -- it is only the
// tile that Steam cannot place. So the appid is carried on the tile, and the
// same endpoint PlayCount uses is asked directly.
//
// Nothing is drawn unless there is a real number to draw:
//
//   not one of our tiles      -> the page is not ours to write on
//   the game is not on Steam  -> about one game in six, nobody to ask
//   Steam had no answer       -> delisted, region-locked, a bad minute
//
// None of those is zero players, and "0 playing" would be a claim in all
// three cases rather than a measurement.

import { type ReactElement } from "react";
import { FaUsers } from "react-icons/fa";

import HeroBadge from "../heroBadge";
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
  const { players: enabled, playersPosition } = useBadgeSettings();
  const players = usePlayersForApp(enabled ? appid : undefined);

  // Our tiles only, and only with a number. See the header for why a missing
  // number is never drawn as zero.
  if (!enabled || !provider || players === undefined) return <></>;

  return (
    <HeroBadge position={playersPosition} accent="#6cc04a">
      <FaUsers size={13} />
      <span style={{ color: "#e8ecf2" }}>
        {pretty(players)} playing
      </span>
    </HeroBadge>
  );
}
