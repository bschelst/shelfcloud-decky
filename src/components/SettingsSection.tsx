// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import {
  ButtonItem,
  Dropdown,
  Focusable,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import { FC } from "react";
import {
  FaCog,
  FaGamepad,
  FaSignOutAlt,
  FaTrashAlt,
} from "react-icons/fa";

import { Collapsible, IconLabel } from "./Collapsible";
import { NotificationLogo } from "./Logo";
import { theme } from "../lib/theme";
import type { Status } from "../lib/types";

const SHARED = -1;

interface SettingsSectionProps {
  status: Status;
  setStatus: (s: Status) => void;
  signedOut: boolean;
  busy: boolean;
  shelves: {
    ok: boolean;
    shelves?: Array<{ id: number; name: string; games: number }>;
    shelf_id?: number | null;
    device_name?: string | null;
  } | null;
  confirmClear: boolean;
  confirmSignOut: boolean;
  onRemoveAll: () => void;
  onSignOut: () => void;
  onApplyOverrides: () => void;
  onSetSetting: (key: string, value: unknown) => Promise<Status>;
  onSetShelf: (shelfId: number | null) => Promise<{ ok: boolean; shelf_id?: number | null; error?: string }>;
  onShelfChanged: () => void;
  toaster: { toast: (opts: { title: string; body: string; logo?: React.ReactNode }) => void };
}

export const SettingsSection: FC<SettingsSectionProps> = ({
  status, setStatus, signedOut, busy, shelves,
  confirmClear, confirmSignOut,
  onRemoveAll, onSignOut, onApplyOverrides,
  onSetSetting, onSetShelf, onShelfChanged, toaster,
}) => (
  <Collapsible title="Settings" icon={<FaCog size={14} />}>
    <PanelSectionRow>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Browser</div>
        <Dropdown
          selectedOption={status.browser?.app_id}
          rgOptions={status.browsers.map((b) => ({ data: b.app_id, label: b.label }))}
          onChange={(option) => { void onSetSetting("browser", option.data).then(setStatus); }}
        />
      </Focusable>
    </PanelSectionRow>

    <PanelSectionRow>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Sync every</div>
        <Dropdown
          disabled={signedOut}
          selectedOption={status.sync_minutes}
          rgOptions={status.sync_choices.map((m) => ({
            data: m,
            label: m === 60 ? "1 hour" : `${m} min`,
          }))}
          onChange={(option) => { void onSetSetting("sync_minutes", option.data).then(setStatus); }}
        />
      </Focusable>
    </PanelSectionRow>

    <PanelSectionRow>
      <ToggleField
        label="A tile per service"
        description={
          "Off, a game gets one tile on whichever service suits it best. On, "
          + "it gets one for every service it plays on, so it appears in each "
          + "of those collections. This makes the library much larger."
        }
        disabled={signedOut || busy}
        checked={status.sync_all_providers === true}
        onChange={(value) => {
          void onSetSetting("sync_all_providers", value).then(setStatus);
        }}
      />
    </PanelSectionRow>

    {shelves?.ok && (shelves.shelves?.length ?? 0) > 0 && (
      <PanelSectionRow>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ fontSize: "0.75em", opacity: 0.7 }}>
            {shelves.device_name ? `Shelf for ${shelves.device_name}` : "Use shelf"}
          </div>
          <Dropdown
            disabled={signedOut || busy}
            selectedOption={shelves.shelf_id ?? SHARED}
            rgOptions={[
              { data: SHARED, label: "Shared games" },
              ...(shelves.shelves ?? []).map((shelf) => ({
                data: shelf.id,
                label: `${shelf.name} (${shelf.games})`,
              })),
            ]}
            onChange={(option) => {
              const next = option.data === SHARED ? null : Number(option.data);
              void onSetShelf(next).then((result) => {
                if (!result.ok) {
                  toaster.toast({ title: "Shelfcloud", body: result.error || "Could not change shelf",
                                  logo: <NotificationLogo /> });
                  return;
                }
                onShelfChanged();
              }).catch((error: unknown) => {
                toaster.toast({ title: "Shelfcloud", body: String(error),
                                logo: <NotificationLogo /> });
              });
            }}
          />
        </Focusable>
      </PanelSectionRow>
    )}

    <PanelSectionRow>
      <ButtonItem
        layout="below"
        onClick={onApplyOverrides}
      >
        <IconLabel icon={<FaGamepad size={12} />}>Fix gamepad permissions</IconLabel>
      </ButtonItem>
    </PanelSectionRow>

    <div style={{
      height: 1,
      background: theme.border,
      margin: "16px 16px 4px",
    }} />

    <PanelSectionRow>
      <ButtonItem layout="below" disabled={busy || signedOut} onClick={onRemoveAll}>
        <IconLabel
          icon={<FaTrashAlt size={12} color={theme.bad} />}
        >
          <span style={{
            color: theme.bad,
            fontWeight: confirmClear ? 700 : 500,
            whiteSpace: "nowrap",
          }}>
            {confirmClear ? "Confirm — remove all" : "Remove all games"}
          </span>
        </IconLabel>
      </ButtonItem>
    </PanelSectionRow>

    <PanelSectionRow>
      <ButtonItem layout="below" disabled={signedOut} onClick={onSignOut}>
        <IconLabel icon={<FaSignOutAlt size={12} />}>
          <span style={{
            color: confirmSignOut ? theme.bad : undefined,
            fontWeight: confirmSignOut ? 700 : 500,
          }}>
            {confirmSignOut ? "Confirm — sign out" : "Sign out"}
          </span>
        </IconLabel>
      </ButtonItem>
    </PanelSectionRow>
  </Collapsible>
);
