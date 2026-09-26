// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Putting a small thing over a game's hero, and keeping it there.
//
// Shared because there are two of these now -- which service streams a game,
// and how many people are playing it -- and the hard part is not either
// badge. It is the placement: finding the capsule in a tree that is still
// being built, surviving Steam's fullscreen transition, and measuring against
// the right box.
//
// None of that was reasoned out. It was arrived at against a moving Steam UI,
// through a badge that floated too high, then hopped on load, then hopped
// twice -- and the comments below record which wrong thing was measured each
// time. Copying it for a second badge would have meant re-earning those.

import { appDetailsClasses, appDetailsHeaderClasses } from "@decky/ui";
import { useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";

import { badgePosition } from "../../lib/badgePosition";

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

/** A pill over the hero, placed where the user asked for it. */
export default function HeroBadge(
  { position, accent, children }:
  { position: string; accent: string; children: ReactNode },
): ReactElement {
  // Visible by default: opening a page is not a mutation, so nothing would
  // ever set this true if it started false.
  const [show, setShow] = useState(true);
  const [heroHeight, setHeroHeight] = useState<number | null>(null);
  // Whether the number above came from the real hero art yet.
  //
  // The badge used to draw immediately at the fallback position and then
  // JUMP down once the artwork loaded and the measurement corrected — on
  // every game, every time the page opened. It is kept invisible until the
  // measurement is real, so it appears once, in the right place.
  //
  // Invisible rather than absent: it has to be IN the document to be
  // measured at all — offsetParent is null for an element that is not
  // rendered, and that is the very thing being measured against.
  const [placed, setPlaced] = useState(false);
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
      // The CAPSULE, which is what the ProtonDB medal measures.
      //
      // Its height is settled at first paint. The hero IMAGE is not: the
      // <img> is in the tree long before it has loaded, and an unloaded one
      // has no height — so measuring that drew the badge against nothing and
      // hopped it down when the picture arrived. Three attempts at hiding
      // the hop were three attempts at papering over a problem introduced by
      // measuring the wrong thing. The capsule never moves, so there is
      // nothing to hide.
      const measure = () => {
        const anchor = ref.current?.offsetParent ?? ref.current?.parentElement;
        if (!anchor) return;
        const value = hero.getBoundingClientRect().bottom
                      - anchor.getBoundingClientRect().top;
        if (value <= 0) return;
        setHeroHeight(value);
        setPlaced(true);
      };
      resizes = new ResizeObserver(measure);
      resizes.observe(hero);
      measure();
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

  return (
    <div
      ref={ref}
      style={{
        position: "absolute",
        zIndex: 10,
        ...badgePosition(position, heroHeight),
        display: show ? "flex" : "none",
        // See `placed`: invisible until it knows where it goes, so it does
        // not appear in one spot and hop to another.
        visibility: placed ? "visible" : "hidden",
        alignItems: "center",
        gap: 6,
        padding: "5px 10px",
        borderRadius: 999,
        // Dark and translucent rather than a brand colour: it sits on artwork
        // nobody chose, and a solid block of colour over a screenshot reads
        // as damage. The colour is the icon.
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
      {children}
    </div>
  );
}
