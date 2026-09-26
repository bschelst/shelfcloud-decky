// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A small mark on a game's page saying which service streams it.
//
// Shaped after the ProtonDB plugin's medal, which solves the same problem on
// the same page: sit over the hero image, survive the fullscreen transition,
// and be placed by the user rather than by us. The mechanics that are not
// obvious — the mutation observer, measuring the hero to place a bottom
// badge — are taken from there deliberately, because they were arrived at
// against a moving Steam UI and re-deriving them would be re-earning scars.
//
// What is different: a cloud tile is a NON-Steam shortcut, so the app id in
// the route is the shortcut's own and needs no lookup against Steam's
// catalogue. And the badge is shown only for tiles this plugin made — on
// anything else it renders nothing at all, which is what makes it mean
// "this one is a cloud game".

import { type ReactElement } from "react";
import { FaAmazon, FaCloud, FaXbox } from "react-icons/fa";
import { SiNvidia } from "react-icons/si";

import HeroBadge from "../heroBadge";
import { LogoMono } from "../Logo";
import { providerInfo } from "../../lib/theme";
import { useParams } from "../../lib/useParams";
import { useBadgeSettings, useProviderForApp } from "../../lib/badgeData";


/** The mark for each service.
 *
 *  Boosteroid has no icon in react-icons and no wordmark we may redistribute,
 *  so it gets a generic cloud. The two website providers get the Shelfcloud
 *  mark, because what they have in common is that WE published them. */
function ProviderIcon({ provider, size }: { provider: string; size: number }) {
  switch (provider) {
    case "geforce_now": return <SiNvidia size={size} />;
    case "xbox_cloud": return <FaXbox size={size} />;
    case "amazon_luna": return <FaAmazon size={size} />;
    case "shelfcloud":
    case "custom":
      // LogoMono sizes itself in em, so the font-size around it is the knob.
      return <span style={{ fontSize: size, lineHeight: 0 }}><LogoMono /></span>;
    default: return <FaCloud size={size} />;
  }
}

/** Find the hero capsule, which is what a bottom badge is measured against.
 *
 *  Walks from our own node rather than querying the document: there is one of
 *  these per app page and more than one app page can exist in the tree during
 *  a transition, so a global query can return somebody else's. */
export default function ProviderBadge(): ReactElement {
  const { appid } = useParams<{ appid: string }>();
  const provider = useProviderForApp(appid);
  const { enabled, position, loaded } = useBadgeSettings();

  // Nothing at all unless this is one of ours. A badge on every game would
  // say nothing; the whole meaning is "this tile streams".
  // Nothing until the settings are READ. Drawing on the default and
  // correcting a moment later is how a switched-off badge still flashed up
  // on every page open.
  if (!loaded || !enabled || !provider) return <></>;

  const { label, color: accent } = providerInfo(provider);
  return (
    <HeroBadge position={position} accent={accent}>
      <ProviderIcon provider={provider} size={14} />
      <span style={{ color: "#e8ecf2" }}>{label}</span>
    </HeroBadge>
  );
}
