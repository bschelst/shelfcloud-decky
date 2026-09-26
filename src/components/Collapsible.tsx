// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Collapsible panel sections, matching the layout used by steamstatus-decky:
// a focusable header with the section icon on the left and a chevron on the
// right, a soft tint while focused, and an indented body when open.
//
// Built from Focusable rather than Field because the header needs two things
// on one row — icon+label pushed left, chevron pushed right — and Field owns
// its own row layout. Focusable gives the controller behaviour with none of
// the layout opinions.
//
// Collapsed by default: this is read on a 7-inch screen with a thumbstick, so
// opening everything at once buries the controls underneath.

import { Focusable } from "@decky/ui";
import { createContext, ReactNode, useContext, useState } from "react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";

import { nextOpenId } from "../lib/accordion";
import { theme } from "../lib/theme";

/** Shared "which one is open" for a set of sections, or null when standalone.
 *
 *  Optional on purpose. A Collapsible used on its own -- NoBrowser is one --
 *  keeps its own state and behaves exactly as before; only sections rendered
 *  inside a group answer to each other.
 */
const GroupContext = createContext<{
  openId: string | null;
  setOpenId: (id: string | null) => void;
} | null>(null);

/** Let only one of the sections inside be open at a time.
 *
 *  The panel is read on a 7-inch screen with a thumbstick. Settings, Links and
 *  About are each long enough that two of them open at once pushes the rest
 *  off the bottom, and the thing you were reaching for moves while you reach
 *  for it. Opening one now closes the last, so the panel's height stays in the
 *  same neighbourhood however you got there.
 */
export function CollapsibleGroup({ children }: { children: ReactNode }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <GroupContext.Provider value={{ openId, setOpenId }}>
      {children}
    </GroupContext.Provider>
  );
}

export function Collapsible({
  title,
  icon,
  accent = theme.accent,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon: ReactNode;
  /** Icon and focus tint colour. Lets a section carry its own identity. */
  accent?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const group = useContext(GroupContext);
  const [localOpen, setLocalOpen] = useState(defaultOpen);
  const [focused, setFocused] = useState(false);

  // The title doubles as the identity. These are headings a person reads, so
  // they are already distinct within a group -- and a separate id prop would
  // be one more thing to keep in step with the label for no gain.
  const open = group ? group.openId === title : localOpen;
  const toggle = () => {
    if (group) group.setOpenId(nextOpenId(group.openId, title));
    else setLocalOpen((v) => !v);
  };

  return (
    <div style={{ marginTop: 8 }}>
      <Focusable
        onActivate={toggle}
        onFocus={(e: React.FocusEvent) => {
          if (e.currentTarget === e.target) setFocused(true);
        }}
        onBlur={(e: React.FocusEvent) => {
          if (e.currentTarget === e.target) setFocused(false);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: 8,
          cursor: "pointer",
          borderRadius: 4,
          background: focused ? theme.tint(accent, 0.12) : "transparent",
          transition: "background 0.2s ease",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ display: "flex", color: accent }}>{icon}</span>
          <span>{title}</span>
        </div>
        {open ? <FaChevronUp size={12} /> : <FaChevronDown size={12} />}
      </Focusable>

      {open && <div style={{ paddingLeft: 12 }}>{children}</div>}
    </div>
  );
}

/** Button label with an icon, so every action in a section reads the same. */
export function IconLabel({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {icon}
      <span>{children}</span>
    </div>
  );
}
