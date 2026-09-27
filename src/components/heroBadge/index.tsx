// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A small badge over a game's hero, kept in place. Shared by the two badges
// (which service streams it, how many people play it). The hard part is the
// placement: find the capsule while the tree is still being built, survive
// Steam's fullscreen transition, and measure against the right box.

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
  // Visible by standaard: opening a page is not a mutation, so nothing would
  // ever turn it on if it started off.
  const [show, setShow] = useState(true);
  const [heroHeight, setHeroHeight] = useState<number | null>(null);
  // True once we have a real measurement. Kept invisible until then so it does
  // not draw at the fallback spot and jump. Invisible, not gone: it has to be
  // in the document to be measured (offsetParent is null otherwise).
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
        // tree not built yet, so try again in a moment
        retry = setTimeout(attach, 100);
        return;
      }

      // Steam animates in and out of the fullscreen hero, and an absolute
      // child looks wrong over that. Hide for the transition, come back after.
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

      // Distance from the hero capsule's BOTTOM to the top of whatever `top`
      // is measured against, not its height. The two match only when the offset
      // parent starts where the hero does, which twice it did not, so the badge
      // floated high above. So we measure both boxes and subtract.
      //
      // The capsule, not the hero image: the image has no height until it
      // loads, so measuring it drew against nothing and hopped on load. The
      // capsule is settled at first paint and never moves.
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
        // see `placed`: invisible until it knows where it goes
        visibility: placed ? "visible" : "hidden",
        alignItems: "center",
        gap: 6,
        padding: "5px 10px",
        borderRadius: 999,
        // dark and see-through, not a brand colour: it sits on artwork nobody
        // chose. the colour is the icon.
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
