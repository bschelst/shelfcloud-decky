// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Shown when no supported browser is installed, the one state where Shelfcloud
// cannot work. We install one from Flathub as `deck` (a --user install needs no
// root, and Game Mode cannot show a password prompt). Desktop Mode instructions
// stay below the buttons for when the install fails or there is no network.

import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { FaDownload, FaExclamationTriangle } from "react-icons/fa";

import { IconLabel } from "./Collapsible";
import { theme } from "../lib/theme";
import type { InstallableBrowser, InstallState } from "../lib/types";

export interface NoBrowserProps {
  installable: InstallableBrowser[];
  install: InstallState;
  onInstall: (appId: string) => void;
  onDismiss: () => void;
}

export function NoBrowser({ installable, install, onInstall, onDismiss }: NoBrowserProps) {
  const busy = install.running;
  const failed = install.done && !!install.error;

  return (
    <PanelSection title="Shelfcloud">
      <PanelSectionRow>
        <div style={{ fontSize: "0.85em", lineHeight: 1.5 }}>
          Shelfcloud needs a browser to play cloud games in. SteamOS does not
          come with one.
        </div>
      </PanelSectionRow>

      {/* While installing: what is happening, and nothing to press by mistake. */}
      {busy && (
        <PanelSectionRow>
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "10px 12px",
            borderRadius: 6,
            background: theme.accentSoft,
            borderLeft: `3px solid ${theme.accent}`,
            fontSize: "0.85em",
          }}>
            <FaDownload color={theme.accent} />
            <span>{install.message}</span>
          </div>
        </PanelSectionRow>
      )}

      {failed && (
        <PanelSectionRow>
          <div style={{
            padding: "10px 12px",
            borderRadius: 6,
            background: "rgba(255, 107, 107, 0.10)",
            borderLeft: `3px solid ${theme.bad}`,
            fontSize: "0.82em",
            lineHeight: 1.5,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <FaExclamationTriangle color={theme.bad} />
              <strong>{install.message}</strong>
            </div>
            {/* the real reason, usually "no network" or "Flathub unreachable" */}
            <div style={{ color: theme.textDim, wordBreak: "break-word" }}>
              {install.error}
            </div>
          </div>
        </PanelSectionRow>
      )}

      {/* One button per browser with its reason right beneath it. Hidden while
          installing so nothing can be pressed twice. */}
      {!busy && installable.map((browser) => (
        <PanelSectionRow key={browser.app_id}>
          <div>
            <ButtonItem layout="below" onClick={() => onInstall(browser.app_id)}>
              {/* nowrap: a wrapped ButtonItem label grows the row from 40 to
                  60px, and these are the longest labels in the plugin */}
              <IconLabel icon={<FaDownload size={13} color={theme.accent} />}>
                <span style={{ whiteSpace: "nowrap" }}>Install {browser.label}</span>
              </IconLabel>
            </ButtonItem>
            <div style={{
              fontSize: "0.76em",
              color: theme.textDim,
              lineHeight: 1.45,
              padding: "4px 4px 2px",
            }}>
              {browser.recommended && (
                <span style={{ color: theme.accentText, fontWeight: 600 }}>
                  Recommended.{" "}
                </span>
              )}
              {browser.note}
            </div>
          </div>
        </PanelSectionRow>
      ))}

      {!busy && installable.length > 0 && (
        <PanelSectionRow>
          {/* inset to match the per-browser notes above it */}
          <div style={{
            fontSize: "0.76em",
            color: theme.textFaint,
            lineHeight: 1.45,
            padding: "4px 4px 2px",
          }}>
            Downloaded from Flathub — the same source the Discover store uses.
            About 10 MB.
          </div>
        </PanelSectionRow>
      )}

      {failed && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={onDismiss}>Try again</ButtonItem>
        </PanelSectionRow>
      )}

      {/* Always here: the fallback for no network or a Flathub outage. No icon --
          IconLabel centres it against the label, which looks wrong on wrapped
          text. */}
      {!busy && (
        <PanelSectionRow>
          <div style={{
            fontSize: "0.76em",
            color: theme.textFaint,
            lineHeight: 1.45,
            paddingTop: 6,
            borderTop: `1px solid ${theme.border}`,
            marginTop: 4,
          }}>
            Or install one yourself from the Discover store in Desktop Mode,
            then reopen this panel.
          </div>
        </PanelSectionRow>
      )}

      <div style={{ height: 20 }} />
    </PanelSection>
  );
}
