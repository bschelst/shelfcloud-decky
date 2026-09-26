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
  const { players: enabled, playersPosition, loaded } = useBadgeSettings();
  const players = usePlayersForApp(enabled ? appid : undefined);

  // Switched on, and one of our tiles. Never drawn on an ASSUMED setting --
  // see providerBadge: that is how a badge that was switched off still
  // flashed up on every page open and then vanished.
  if (!loaded || !enabled || !provider) return <></>;

  // Once it is switched on, it always shows. A number when there is one, and
  // a dash when there is not -- because "nothing on screen" is the same thing
  // the bug looked like, and there is no way to tell a badge that is thinking
  // from a badge that is broken.
  //
  // A dash rather than a zero. No count comes back for a game that is not on
  // Steam at all (about one in six here), and "0 playing" would be a claim
  // about a game Steam has never heard of rather than a measurement.
  const known = players !== undefined;

  return (
    <HeroBadge position={playersPosition} accent={known ? "#6cc04a" : "#7a8290"}>
      <FaUsers size={13} />
      <span style={{ color: "#e8ecf2" }}>
        {known ? `${pretty(players)} playing` : "\u2013"}
      </span>
    </HeroBadge>
  );
}
