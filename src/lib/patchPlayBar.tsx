// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A small button right after Play on a game page, opening "Play on" with the
// same game on your other cloud services. For our tiles (Fortnite on GeForce
// NOW offers Boosteroid), and for Steam games where Steam draws no dropdown of
// its own (no second PC). Where Steam does draw one, patchPlayMenu adds the
// lines in there and this button stays away, one place is enough.
//
// The Play bar is a class component that Steam does not export, so we take its
// prototype from a live instance (see PlayBarHook). Its `render` is set up lazy:
// the first call defines a fixed, non-writable `render` on the instance (mobx
// observer around the real one) and every later render goes there. Patching the
// prototype alone only sees that first call. So during that first call we catch
// the defineProperty for `render` on that one instance and hand over a wrapped
// function. Only for that call, only for that object and property.
//
// Consequence: the instance that was already on screen when we patched keeps
// its old render. The button shows from the next game page you open.

import { findClassModule, Focusable, Menu, showContextMenu } from "@decky/ui";
import {
  Children, cloneElement, isValidElement, useEffect, useRef, useState,
  type ReactElement, type ReactNode,
} from "react";

import { playOptionsNow, providerNow } from "./badgeData";
import type { CloudOption } from "./cloudOptions";
import { cloudMenuItems, currentMenuItem } from "./playOptionsUi";
import {
  ARROW_ICON, fallbackArrowStyle, PLAY_SQUARED, shouldLookAgain, steamDrawsArrow,
} from "./steamArrow";

/* Steam's tree, fibers and the class are untyped and not ours. */
/* eslint-disable @typescript-eslint/no-explicit-any */

/** False after unload. Instances patched before keep our wrapper (Steam made it
 *  non-writable) until they unmount, so the wrapper checks this and steps aside. */
let active = false;
let restoreProto: (() => void) | null = null;

/** How far we look through the page's fiber tree for the Play bar. The page
 *  has some hundreds of fibers, this is a guard against a runaway loop. */
const FIBER_BUDGET = 8000;

/** Steam's own class names for the Play button with its dropdown arrow. By
 *  name, not hash: the hashes change with every Steam build, the names not.
 *  Looked up on first use, the module is not there before Steam's UI is. */
interface StreamingClasses { StreamingSelector: string; ShowingStreaming: string; ButtonChild: string }
let streamingClasses: StreamingClasses | null = null;
let lastMiss = 0;

/** Steam's classes as Decky knows them, or null. A miss is not kept for ever:
 *  Decky 4 on a Steam beta had the module not indexed at all, then we draw our
 *  own arrow and ask again a bit later, see shouldLookAgain. */
function getStreamingClasses(): StreamingClasses | null {
  if (streamingClasses || !shouldLookAgain(lastMiss, Date.now())) return streamingClasses;
  const found = findClassModule((m: any) =>
    typeof m?.StreamingSelector === "string"
    && typeof m?.ShowingStreaming === "string"
    && typeof m?.ButtonChild === "string") as StreamingClasses | void;
  streamingClasses = found || null;
  if (!streamingClasses) lastMiss = Date.now();
  return streamingClasses;
}

/** The menu: on a tile the service it is on first (with a check), then the others. */
function openMenu(target: EventTarget, current: CloudOption | null, options: CloudOption[]): void {
  showContextMenu(
    <Menu label="Play from">
      {current ? currentMenuItem(current) : null}
      {cloudMenuItems(options)}
    </Menu>,
    target,
  );
}

/** The arrow after Play. Steam's own classes when Decky has them, so it joins
 *  Play into one button like Steam's arrow does, and the same triangle Steam
 *  draws. Without them our own look, copied from the Play button. */
function PlayFromArrow(
  { classes, current, options }:
  { classes: StreamingClasses | null; current: CloudOption | null; options: CloudOption[] },
): ReactElement {
  const [focused, setFocused] = useState(false);
  return (
    <Focusable
      className={classes ? `${classes.StreamingSelector} ${classes.ButtonChild}` : undefined}
      style={classes ? undefined : fallbackArrowStyle(focused)}
      // Steam's arrow shows focus by going white, not with the ring around it
      noFocusRing={!classes}
      onGamepadFocus={() => setFocused(true)}
      onGamepadBlur={() => setFocused(false)}
      onMouseEnter={() => setFocused(true)}
      onMouseLeave={() => setFocused(false)}
      onActivate={(event) => openMenu(event.currentTarget ?? window, current, options)}
      onClick={(event) => openMenu(event.currentTarget ?? window, current, options)}
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36" fill="none"
           // Steam's classes size it to 12px, our own look has to say it itself
           width={classes ? undefined : ARROW_ICON} height={classes ? undefined : ARROW_ICON}>
        <path d="M17.98 26.54L3.20996 11.77H32.75L17.98 26.54Z" fill="currentColor" />
      </svg>
    </Focusable>
  );
}

/** Play with its right corners square, so it joins our arrow. Left alone when
 *  it is not an element, a style we can not set is no reason to fail. */
function squarePlay(play: ReactNode): ReactNode {
  if (!isValidElement(play)) return play;
  const style = (play.props as { style?: Record<string, unknown> }).style;
  return cloneElement(play, { style: { ...style, ...PLAY_SQUARED } } as any);
}

/** Steam's Play bar output, with our button put right after the Play button. */
function withButton(self: any, rendered: ReactNode): ReactNode {
  if (!active || !isValidElement(rendered)) return rendered;
  try {
    const appid = self?.props?.overview?.appid;
    if (typeof appid !== "number") return rendered;
    const options = playOptionsNow(appid);
    if (!options.length) return rendered;
    // Steam draws its own arrow here, patchPlayMenu puts our lines in its menu.
    // Checked on the render itself: bShowStreamingSelector stays true with the
    // streaming PC off, and then there was no arrow at all.
    if (steamDrawsArrow(rendered, self.ShowStreamingMenu)) return rendered;

    // null when Decky does not know them (Decky 4 on a Steam beta), then the
    // arrow draws itself, see PlayFromArrow
    const classes = getStreamingClasses();
    const props = rendered.props as { children?: ReactNode; className?: string };
    const kids = Children.toArray(props.children);
    if (!kids.length) return rendered;

    const provider = providerNow(appid);
    const current = provider ? { appId: String(appid), provider } : null;
    return cloneElement(
      rendered,
      // the class Steam puts on the row when it has its own arrow: squares the
      // right side of Play so Play and arrow read as one button. Only with
      // Steam's classes, without them the row stays as it is.
      (classes
        ? { className: `${props.className ?? ""} ${classes.ShowingStreaming}`.trim() }
        : {}) as any,
      // without Steam's classes we square Play's right corners ourself, what
      // ShowingStreaming does otherwise
      classes ? kids[0] : squarePlay(kids[0]),
      <PlayFromArrow key="shelfcloud-play-from" classes={classes} current={current} options={options} />,
      ...kids.slice(1),
    );
  } catch (error) {
    // never break Steam's Play button over our extra one
    console.error("[shelfcloud] play bar button failed", error);
    return rendered;
  }
}

/** Patch the class once. The lazy render on the prototype is wrapped so the
 *  render it defines on each new instance is ours. */
function patchPrototype(proto: any): void {
  if (restoreProto || typeof proto?.render !== "function") return;
  const lazyRender = proto.render;

  proto.render = function patchedLazyRender(this: any, ...args: unknown[]) {
    return catchRenderDefine(this, () => lazyRender.apply(this, args));
  };
  restoreProto = () => { proto.render = lazyRender; };
}

/** Run `run` (Steam's lazy render) and, if it defines `render` on `instance`,
 *  give it our wrapped version. defineProperty is back to normal right after,
 *  also when Steam throws. */
function catchRenderDefine(instance: object, run: () => unknown): unknown {
  const realDefine = Object.defineProperty;
  Object.defineProperty = function (obj: any, prop: PropertyKey, desc: PropertyDescriptor) {
    if (obj === instance && prop === "render" && typeof desc?.value === "function") {
      const inner = desc.value;
      desc = { ...desc, value: function (this: any, ...a: unknown[]) {
        return withButton(this, inner.apply(this, a));
      } };
    }
    return realDefine.call(Object, obj, prop, desc);
  } as typeof Object.defineProperty;
  try {
    return run();
  } finally {
    Object.defineProperty = realDefine;
  }
}

/** The Play bar instance in the page, searched down from `node`'s fiber. It is
 *  the class with ShowStreamingMenu on its prototype. */
function findPlayBar(node: Element | null): any | null {
  const start = node?.parentElement;
  if (!start) return null;
  const key = Object.keys(start).find((k) => k.startsWith("__reactFiber"));
  const root = key ? (start as any)[key] : null;
  const stack: any[] = root ? [root] : [];
  for (let seen = 0; stack.length && seen < FIBER_BUDGET; seen++) {
    const fiber = stack.pop();
    const instance = fiber?.stateNode;
    if (instance && !(instance instanceof Node)
        && typeof Object.getPrototypeOf(instance)?.ShowStreamingMenu === "function") {
      return instance;
    }
    if (fiber?.sibling) stack.push(fiber.sibling);
    if (fiber?.child) stack.push(fiber.child);
  }
  return null;
}

/** Rendered on every game page (by patchLibraryApp). Finds the Play bar once
 *  per session to get at its class. Draws nothing. */
export function PlayBarHook(): ReactElement {
  const ref = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!active || restoreProto) return undefined;   // off, or done already
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const attempt = () => {
      const instance = findPlayBar(ref.current);
      if (instance) {
        patchPrototype(Object.getPrototypeOf(instance));
        return;
      }
      // the Play bar can come a moment after the page
      if (++tries < 30) timer = setTimeout(attempt, 100);
    };
    timer = setTimeout(attempt, 0);
    return () => { if (timer) clearTimeout(timer); };
  }, []);

  return <span ref={ref} style={{ display: "none" }} />;
}

/** Switch the button on. The prototype is patched on the first game page. */
export function startPlayBar(): void {
  active = true;
}

/** Plugin unload: put the prototype back and let patched instances pass through. */
export function stopPlayBar(): void {
  active = false;
  restoreProto?.();
  restoreProto = null;
}
