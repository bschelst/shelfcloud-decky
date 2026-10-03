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
  FaUsers,
} from "react-icons/fa";

import { Collapsible, IconLabel } from "./Collapsible";
import { NotificationLogo } from "./Logo";
import { applyTilePerService } from "../lib/settings";
import { cornersExcept } from "../lib/badgePosition";
import { invalidateBadgeData } from "../lib/badgeData";
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
  /** Run after "a tile per service" is switched ON, to pull in games the user
   *  can play but never added. Not called on OFF, which only removes tiles. */
  onTilePerServiceEnabled?: () => void;
  onSetShelf: (shelfId: number | null) => Promise<{ ok: boolean; shelf_id?: number | null; error?: string }>;
  onShelfChanged: () => void;
  toaster: { toast: (opts: { title: string; body: string; logo?: React.ReactNode }) => void };
}

export const SettingsSection: FC<SettingsSectionProps> = ({
  status, setStatus, signedOut, busy, shelves,
  confirmClear, confirmSignOut,
  onRemoveAll, onSignOut, onApplyOverrides,
  onSetSetting, onTilePerServiceEnabled, onSetShelf, onShelfChanged, toaster,
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
            label: m === 0 ? "Off" : m === 60 ? "1 hour" : `${m} min`,
          }))}
          onChange={(option) => { void onSetSetting("sync_minutes", option.data).then(setStatus); }}
        />
      </Focusable>
    </PanelSectionRow>

    <PanelSectionRow>
      {/* Padded top and bottom, unlike the rows around it: this one ends in a
          paragraph, which otherwise runs into the labels above and below. */}
      <div style={{ padding: "10px 0" }}>
        <ToggleField
          label="A tile per service"
          description={
            "Off, a game gets one tile on whichever service suits it best. On, "
            + "it gets one for every service it plays on, so it appears in each "
            + "of those collections. Switching it on also adds the games your "
            + "subscriptions cover but you have never added. This makes the "
            + "library much larger and the first sync a long one."
          }
          // Not disabled during a sync. It used to be, and a sync runs every few
          // minutes, so tapping it then looked like a setting that would not turn
          // on. Storing it is safe any time; only the bulk add waits, and it does.
          disabled={signedOut}
          checked={status.sync_all_providers === true}
          onChange={(value) => {
            void applyTilePerService(value, {
              setSetting: onSetSetting,
              setStatus,
              onEnabled: onTilePerServiceEnabled,
              busy,
            });
          }}
        />
      </div>
    </PanelSectionRow>

    <PanelSectionRow>
      <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Interface size</div>
        <Dropdown
          selectedOption={String(status.ui_scale ?? "auto")}
          rgOptions={[
            { data: "auto", label: "Auto" },
            ...[1, 1.25, 1.5, 2].map((s) => ({
              data: String(s),
              label: `${Math.round(s * 100)}%`,
            })),
          ]}
          onChange={(option) => {
            void onSetSetting("ui_scale", option.data).then(setStatus);
          }}
        />
        {/* Show what was detected: "Auto" is otherwise a number taken on trust,
            and a wrong guess only shows up as a wrong-sized UI. */}
        {status.ui_scale_detected && (
          <div style={{
            fontSize: "0.7em",
            opacity: 0.55,
            lineHeight: 1.4,
            paddingTop: 2,
          }}>
            {status.ui_scale_detected}
          </div>
        )}
      </Focusable>
    </PanelSectionRow>

    <PanelSectionRow>
      <div style={{ padding: "10px 0" }}>
        <ToggleField
          label="Provider badge"
          description={
            "Shows which service a game streams from, on its page in the "
            + "library. Only on the games Shelfcloud added, so it is also how "
            + "you tell a cloud game from one that is installed."
          }
          checked={status.badge_enabled !== false}
          onChange={(value) => {
            void onSetSetting("badge_enabled", value).then((next) => {
              setStatus(next);
              // the badge caches this for ~30s; a setting just changed must
              // not wait that out
              invalidateBadgeData();
            });
          }}
        />
      </div>
    </PanelSectionRow>

    <PanelSectionRow>
      <div style={{ padding: "0 0 10px" }}>
        <ToggleField
          label="Badge on the highlighted card"
          description={
            "The same mark on the card you have selected, on the home page "
            + "and in the library grid. Only the highlighted one carries it — "
            + "a mark on every card would be noise."
          }
          checked={status.badge_on_cards !== false}
          onChange={(value) => {
            void onSetSetting("badge_on_cards", value).then((next) => {
              setStatus(next);
              invalidateBadgeData();
            });
          }}
        />
      </div>
    </PanelSectionRow>

    <PanelSectionRow>
      <div style={{ width: "100%" }}>
        <ToggleField
          label={<IconLabel icon={<FaUsers size={12} />}>Players online</IconLabel>}
          description={
            "How many people are playing the game on Steam right now. Cloud "
            + "tiles are not Steam games, so Steam cannot show this itself. "
            + "Games that are not on Steam show nothing."
          }
          checked={status.players_enabled === true}
          onChange={(value) => {
            void onSetSetting("players_enabled", value).then((next) => {
              setStatus(next);
              invalidateBadgeData();
            });
          }}
        />
      </div>
    </PanelSectionRow>

    {status.players_enabled === true && (
      <PanelSectionRow>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Players corner</div>
          <Dropdown
            selectedOption={status.players_position ?? "tr"}
            rgOptions={cornersExcept(
              status.badge_enabled !== false ? status.badge_position ?? "bl" : undefined,
              status.players_position ?? "tr")}
            onChange={(option) => {
              void onSetSetting("players_position", option.data).then((next) => {
                setStatus(next);
                invalidateBadgeData();
              });
            }}
          />
        </Focusable>
      </PanelSectionRow>
    )}

    {(status.badge_enabled !== false || status.badge_on_cards !== false) && (
      <PanelSectionRow>
        <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Badge corner</div>
          <Dropdown
            selectedOption={status.badge_position ?? "bl"}
            rgOptions={cornersExcept(
              status.players_enabled === true ? status.players_position ?? "tr" : undefined,
              status.badge_position ?? "bl")}
            onChange={(option) => {
              void onSetSetting("badge_position", option.data).then((next) => {
                setStatus(next);
                invalidateBadgeData();
              });
            }}
          />
        </Focusable>
      </PanelSectionRow>
    )}

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
