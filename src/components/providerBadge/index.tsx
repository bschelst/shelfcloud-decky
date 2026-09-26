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

import { appDetailsClasses, appDetailsHeaderClasses } from "@decky/ui";
import { useEffect, useRef, useState, type ReactElement } from "react";
import { FaAmazon, FaCloud, FaXbox } from "react-icons/fa";
import { SiNvidia } from "react-icons/si";

import { LogoMono } from "../Logo";
import { providerInfo } from "../../lib/theme";
import { badgePosition } from "../../lib/badgePosition";
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
function findHeroCapsule(node: HTMLDivElement | null): Element | null {
  const siblings = node?.parentElement?.children;
  if (!siblings) return null;

  let header: Element | undefined;
  for (const child of siblings) {
    if (child.className.includes(appDetailsClasses.Header)) { header = child; break; }
  }
  if (!header) return null;

  for (const child of header.children) {
    if (child.className.includes(appDetailsHeaderClasses.TopCapsule)) return child;
  }
  return null;
}

export default function ProviderBadge(): ReactElement {
  const { appid } = useParams<{ appid: string }>();
  const provider = useProviderForApp(appid);
  const { enabled, position } = useBadgeSettings();

  // Visible by default: opening a page is not a mutation, so nothing would
  // ever set this true if it started false.
  const [show, setShow] = useState(true);
  const [heroHeight, setHeroHeight] = useState<number | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let mutations: MutationObserver | null = null;
    let resizes: ResizeObserver | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const retries: Array<ReturnType<typeof setTimeout>> = [];

    const attach = () => {
      const hero = findHeroCapsule(ref.current);
      if (!hero) {
        // The tree is not finished yet. Retrying beats giving up: this
        // component mounts as part of the render it is measuring.
        retry = setTimeout(attach, 100);
        return;
      }

      // Steam animates in and out of the fullscreen hero, and an absolutely
      // positioned child sits over the animation looking wrong for its
      // duration. Hide for the transition, come back when it settles.
      mutations = new MutationObserver((entries) => {
        for (const entry of entries) {
          if (entry.type !== "attributes" || entry.attributeName !== "class") continue;
          const name = (entry.target as Element).className;
          const moving =
            name.includes(appDetailsHeaderClasses.FullscreenEnterStart) ||
            name.includes(appDetailsHeaderClasses.FullscreenEnterActive) ||
            name.includes(appDetailsHeaderClasses.FullscreenEnterDone) ||
            name.includes(appDetailsHeaderClasses.FullscreenExitStart) ||
            name.includes(appDetailsHeaderClasses.FullscreenExitActive);
          const settled = name.includes(appDetailsHeaderClasses.FullscreenExitDone);
          setShow(!moving || settled);
        }
      });
      mutations.observe(hero, { attributes: true, attributeFilter: ["class"] });

      // How far the hero's BOTTOM is from the top of whatever `top` is
      // measured against — not the hero's height.
      //
      // Those are the same number only when the badge's offset parent starts
      // exactly where the hero does, and twice now they have not: the badge
      // floated far above the edge with no obvious reason, because a height
      // was being used as a position. Both boxes are measured and subtracted,
      // which is true wherever either one happens to start.
      //
      // The hero IMAGE rather than the capsule around it, too — measured on
      // hardware, the capsule reported 342px where the image inside it was
      // 360, so the capsule's edge is not the edge anyone sees.
      // Searched from the ANCHOR, not from inside the capsule.
      //
      // The hero image is not a descendant of the element findHeroCapsule
      // returns — measured on hardware, the capsule is 342px tall and the
      // image is 360, sitting in a different branch. Looking for it inside
      // the capsule therefore always missed and always fell back to the
      // capsule, which is the whole 18px the badge kept floating by.
      const heroArt = (): Element => {
        const scope = ref.current?.offsetParent ?? ref.current?.parentElement;
        return scope?.querySelector('img[src*="_hero"]') ?? hero;
      };
      let watched: Element | null = null;
      const measure = () => {
        const anchor = ref.current?.offsetParent ?? ref.current?.parentElement;
        if (!anchor) return;
        const art = heroArt();
        setHeroHeight(art.getBoundingClientRect().bottom
                      - anchor.getBoundingClientRect().top);

        // Watch the ART once it exists, not only the capsule.
        //
        // This measured once, at mount, and then never again: the hero image
        // had not loaded yet so the lookup fell back to the capsule, and the
        // capsule's own box never changes, so the ResizeObserver had nothing
        // to report. The badge kept the first wrong answer for the life of
        // the page — which is why two correct-looking fixes moved it by
        // nothing at all.
        if (art !== watched && art !== hero) {
          if (watched) resizes?.unobserve(watched);
          resizes?.observe(art);
          watched = art;
        }
      };
      resizes = new ResizeObserver(measure);
      resizes.observe(hero);
      measure();

      // And a few more attempts, because artwork arrives when it arrives.
      // Cheap, bounded, and it stops as soon as the answer stops changing —
      // setHeroHeight with an unchanged number is not a re-render.
      for (const delay of [150, 400, 1000, 2500]) {
        retries.push(setTimeout(measure, delay));
      }
    };

    // After a tick, so the ref is attached.
    const start = setTimeout(attach, 0);
    return () => {
      clearTimeout(start);
      if (retry) clearTimeout(retry);
      for (const timer of retries) clearTimeout(timer);
      mutations?.disconnect();
      resizes?.disconnect();
    };
  }, []);

  // Nothing at all unless this is one of ours. A badge on every game would
  // say nothing; the whole meaning is "this tile streams".
  if (!enabled || !provider) return <></>;

  const { label, color: accent } = providerInfo(provider);
  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        zIndex: 10,
        ...badgePosition(position, heroHeight),
        display: show ? "flex" : "none",
        alignItems: "center",
        gap: 6,
        padding: "5px 10px",
        borderRadius: 999,
        // Dark and translucent rather than the provider's colour: it sits on
        // artwork nobody chose, and a solid block of brand colour over a
        // screenshot reads as damage. The colour is the icon.
        background: "rgba(8, 10, 14, 0.78)",
        border: `1px solid ${accent}`,
        color: accent,
        fontSize: 12,
        fontWeight: 600,
        lineHeight: 1,
        whiteSpace: "nowrap",
        pointerEvents: "none",          // never steal a click from the page
        backdropFilter: "blur(6px)",
      }}
    >
      <ProviderIcon provider={provider} size={14} />
      <span style={{ color: "#e8ecf2" }}>{label}</span>
    </div>
  );
}
