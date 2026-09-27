// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A small mark on a game's page saying which service streams it. Shaped after
// the ProtonDB medal, which solves the same placement problem on the same page.
// Shown only for tiles this plugin made, so on anything else it draws nothing --
// which is what makes it mean "this one is a cloud game".

import { type ReactElement } from "react";
import { FaAmazon, FaCloud, FaXbox } from "react-icons/fa";
import { SiNvidia } from "react-icons/si";

import HeroBadge from "../heroBadge";
import { LogoMono } from "../Logo";
import { providerInfo } from "../../lib/theme";
import { useParams } from "../../lib/useParams";
import { useBadgeSettings, useProviderForApp } from "../../lib/badgeData";


/** The mark for each service. Boosteroid has no react-icon, so it gets a plain
 *  cloud. The two website providers get the Shelfcloud mark. */
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

/** The provider badge: nothing unless this is one of our tiles. */
export default function ProviderBadge(): ReactElement {
  const { appid } = useParams<{ appid: string }>();
  const provider = useProviderForApp(appid);
  const { enabled, position, loaded } = useBadgeSettings();

  // Nothing unless this is one of ours (a badge on every game would say
  // nothing), and nothing until the settings are read -- drawing on the default
  // and fixing it a moment later is how a switched-off badge still flashed up.
  if (!loaded || !enabled || !provider) return <></>;

  const { label, color: accent } = providerInfo(provider);
  return (
    <HeroBadge position={position} accent={accent}>
      <ProviderIcon provider={provider} size={14} />
      <span style={{ color: "#e8ecf2" }}>{label}</span>
    </HeroBadge>
  );
}
