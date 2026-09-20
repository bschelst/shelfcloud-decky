# Controller Layouts

How Shelfcloud applies Steam Input controller layouts to non-Steam shortcuts.

## Overview

Each cloud gaming provider has a default controller layout. Websites get "Web
Browser" (trackpad as mouse), streaming providers get a gamepad template or a
custom VDF. The admin can override per-provider or per-game.

Two mechanisms, used together:

- **Built-in templates** (`template://`) — apply live via `SetSelectedConfigForApp`, no restart
- **Custom VDFs** (`custom://`) — downloaded from the backend, written to disk, registered in the configset. **Requires a Steam restart.**

## Architecture

```
Admin UI
  ↓ sets controller_layout on provider_settings or game_availability
Backend (shelfcloud)
  ↓ serves built-in template URL or custom VDF via /api/controller-layout/<name>
  ↓ catalog query resolves: per-game override > provider_settings > code default
Plugin backend (main.py)
  ↓ _to_desired() passes controller_layout to frontend
  ↓ install_controller_layout() writes VDF + registers configset
  ↓ _download_custom_layout() fetches custom:// VDFs from gateway
  ↓ _register_configset() edits configset_controller_neptune.vdf
Plugin frontend (sync.ts)
  ↓ applyControllerConfig() calls SetSelectedConfigForApp for live apply
Steam
  ↓ reads configset at startup for custom VDFs
  ↓ reads SetSelectedConfigForApp live for built-in templates
```

## SetSelectedConfigForApp — the live call

```typescript
SteamClient.Input.SetSelectedConfigForApp(appId, controllerIndex, url, false, 1);
```

### CRITICAL requirements

1. **Controller index is NOT 0.** Find it by type:
   ```typescript
   ControllerStore.GetControllers().find(c => c.eControllerType === 4).nControllerIndex
   ```
   Type 4 = SteamControllerNeptune (the Deck's built-in controller). On this
   Deck it was 15. Wrong index = call silently ignored.

2. **5th argument must be 1** (CONFIG_SELECTION_USER). Any other value (0,
   false, true) returns normally but selects nothing. Confirmed by
   moonlight-steam-sync-decky probe V2.

3. **App must be in appStore** before the call sticks. Poll
   `appStore.GetAppOverviewByAppID(appId)` until it returns something.

4. **Only works live with `template://` URLs.** `autosave://` URLs do not
   take effect until Steam restarts.

## Custom VDFs — the file-based approach

Three steps, ALL required:

### 1. Write the VDF

Destination: `~/.local/share/Steam/steamapps/common/Steam Controller Configs/<uid>/config/<SHORTCUT NAME>/controller_neptune.vdf`

- Directory name is the **shortcut name** (e.g. "eFootball (Boosteroid)"), NOT the numeric app ID
- Path must use the **real path** (`/home/deck/.local/share/Steam/`), NOT the `~/.steam/steam` symlink. Use `os.path.realpath()`.
- The VDF must contain a `"url"` field set to `"autosave://<full real path>"` and an `"export_type"` field. Without these, Steam ignores the file.

### 2. Register in the configset

Edit `configset_controller_neptune.vdf` in the same `config/` directory. Add:

```
"<shortcut name>"
{
    "autosave" "1"
}
```

Without this entry, Steam does not know the per-game config exists and ignores the VDF file.

### 3. Restart Steam

The configset is read at startup. There is no known way to force a re-read without restarting. This is an open bug: `docs/bugs/controller-layout-no-live-apply-custom.md`.

## What does NOT work

- `SetSelectedConfigForApp` with `autosave://` URL — no error, but no effect until restart
- `SetSelectedConfigForApp` with controller index 0 — silently ignored (the Deck's index is not 0)
- `SetSelectedConfigForApp` with 5th arg 0/false/true — silently ignored
- `QueryControllerConfigsForApp` on a shortcut that hasn't been launched — returns zero templates
- VDF file without configset registration — ignored by Steam
- VDF file with the `~/.steam/steam` symlink path — ignored by Steam
- VDF file without `url`/`export_type` fields — ignored by Steam
- VDF in a directory named by app ID instead of shortcut name — ignored by Steam

## Backend setup

### Custom VDFs

Stored in `backend/data/controller_layouts/`. Served at `/api/controller-layout/<name>.vdf`. Managed via admin (upload/delete).

### Provider defaults

In `backend/providers.py`:
```python
LAYOUT_GAMEPAD = "template://controller_neptune_gamepad_fps.vdf"
LAYOUT_WEB_BROWSER = "template://controller_neptune_webbrowser.vdf"
LAYOUT_BOOSTEROID = "custom://boosteroid-cloud.vdf"
```

Overridable at runtime via the `provider_settings` table (admin dashboard) or per-game via `game_availability.controller_layout` (admin game detail page).

### Resolution order

1. `game_availability.controller_layout` (per-game override, set in admin)
2. `provider_settings.controller_layout` (per-provider override, set in admin)
3. Code default in `providers.py`
4. Plugin fallback: `_provider_default_layout()` in `main.py`

## Reference implementations

- `~/Projects/Personal/boosteroid-steamos/src/add-to-steam.py` — **the working reference**. Functions `_install_controller_config` and `_update_configset`.
- `episode6/moonlight-steam-sync-decky` — confirmed CONFIG_SELECTION_USER=1 and controller index by type
