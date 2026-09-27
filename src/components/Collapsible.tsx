// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Collapsible panel sections: a focusable header with icon and chevron, a soft
// tint while focused, an indented body when open. Built on Focusable, not Field,
// so we own the row layout. Collapsed by default: read on a 7-inch screen, so
// opening all at once buries the controls.

import { Focusable } from "@decky/ui";
import { createContext, ReactNode, useContext, useState } from "react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";

import { nextOpenId } from "../lib/accordion";
import { theme } from "../lib/theme";

/** Shared "which one is open" for a set of sections, or null when standalone.
 *  A Collapsible on its own (NoBrowser) keeps its own state. */
const GroupContext = createContext<{
  openId: string | null;
  setOpenId: (id: string | null) => void;
} | null>(null);

/** Let only one section inside be open at a time. Two open at once on a 7-inch
 *  screen push the rest off the bottom, so opening one closes the last. */
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

  // The title is the identity too: these are headings a person reads, so they
  // are already distinct, and a separate id prop would be one more thing to sync.
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
