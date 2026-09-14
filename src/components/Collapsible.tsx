// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
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
import { ReactNode, useState } from "react";
import { FaChevronDown, FaChevronUp } from "react-icons/fa";

import { theme } from "../lib/theme";

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
  const [open, setOpen] = useState(defaultOpen);
  const [focused, setFocused] = useState(false);

  return (
    <div style={{ marginTop: 8 }}>
      <Focusable
        onActivate={() => setOpen((v) => !v)}
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
          padding: "8px 12px",
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
