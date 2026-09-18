// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Shown when no supported browser is installed — the only state in which
// Shelfcloud cannot do its job at all.
//
// It used to be one line of text telling the user to leave Game Mode, open
// Discover, find a browser and come back. That is four context switches to
// fix something the plugin can do in a few seconds: SteamOS ships no browser
// but does ship the Flathub remote, and plugin backends run as `deck`, so a
// --user install needs no root and no password prompt — which matters,
// because Game Mode has no way to show one.
//
// The Desktop Mode instructions are still here, below the buttons, for when
// the install fails or there is no network.

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
            {/* The real reason, not a generic apology: it is usually "no
                network" or "Flathub unreachable", both of which the user can
                act on. */}
            <div style={{ color: theme.textDim, wordBreak: "break-word" }}>
              {install.error}
            </div>
          </div>
        </PanelSectionRow>
      )}

      {/* One button per browser, each with its reason directly beneath it
          rather than in a legend further down — pairing them means the reader
          does not have to hold two lists in their head to choose. Hidden
          entirely while installing, so nothing can be pressed twice. */}
      {!busy && installable.map((browser) => (
        <PanelSectionRow key={browser.app_id}>
          <div>
            <ButtonItem layout="below" onClick={() => onInstall(browser.app_id)}>
              {/* nowrap because a wrapped ButtonItem label grows the row from
                  40px to 60px, and these are the longest labels in the plugin:
                  "Install Google Chrome" measures ~169px against the 187px a
                  button gives its label. It fits, but not by enough to trust a
                  font substitution — and a row that silently changes height is
                  how the Remove-all button went wrong three times. */}
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
          <div style={{ fontSize: "0.76em", color: theme.textFaint, lineHeight: 1.45 }}>
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

      {/* Always reachable: the fallback for no network, a Flathub outage, or
          someone who would rather do it themselves.

          No icon here. IconLabel centres its icon against the label, which
          looks wrong beside three lines of wrapped text — and an icon on a
          footnote earns nothing. */}
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
