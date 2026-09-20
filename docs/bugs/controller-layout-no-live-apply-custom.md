# Custom controller layout does not apply without Steam restart

## Status
Open

## Summary
Custom VDF controller layouts (the `custom://` scheme — VDFs hosted on the
backend and downloaded to the Deck) require a Steam restart before they take
effect. Built-in `template://` layouts apply live via `SetSelectedConfigForApp`.

## What works
- VDF written to `Steam Controller Configs/<uid>/config/<shortcut name>/controller_neptune.vdf`
- `url` field set to `autosave://<real path>` (must use `/home/deck/.local/share/Steam/`, not the `~/.steam/steam` symlink)
- `configset_controller_neptune.vdf` entry registered with `"autosave" "1"`
- After a Steam restart, the layout is active — confirmed on hardware

## What does not work
- `SetSelectedConfigForApp(appId, controllerIndex, "autosave://...", false, 1)` — no error thrown, but the layout does not take effect until Steam restarts
- `SetSelectedConfigForApp(appId, controllerIndex, "template://...", false, 1)` — DOES work live, but only for built-in templates, not custom VDFs

## Verified details (2026-09-21, Steam Deck OLED)
- Controller index must be found by type (eControllerType === 4 for Neptune), not assumed to be 0. On this Deck it was 15.
- 5th argument to SetSelectedConfigForApp must be 1 (CONFIG_SELECTION_USER). 0/false/true all silently do nothing. Confirmed by moonlight-steam-sync-decky probe V2.
- App must be in `appStore.GetAppOverviewByAppID()` before the call sticks.
- `QueryControllerConfigsForApp` returns zero templates for a brand-new shortcut that hasn't been launched, so the config-stream approach (RegisterForControllerConfigInfoMessages) cannot work at tile creation time.

## boosteroid-steamos reference
The working reference implementation (`~/Projects/Personal/boosteroid-steamos/src/add-to-steam.py`) does:
1. Copy VDF to per-game config dir (shortcut name, not app ID)
2. Register in `configset_controller_neptune.vdf`
3. Restart plugin_loader (which reloads the Steam UI)

It does NOT call SetSelectedConfigForApp at all.

## Options to resolve
1. **Restart Steam UI** after applying custom layouts — `SteamClient.Browser.RestartJSContext()` or restart `plugin_loader`. Fast but disruptive to the user.
2. **Use only built-in templates** — `template://` layouts apply live without restart. Covers the common cases (gamepad, web browser) but no custom bindings.
3. **Accept the restart** — show a notification asking the user to restart Steam once after first sync.
4. **Investigate further** — there may be a way to force Steam to re-read the configset without a full restart (e.g. re-registering the controller, toggling a controller setting). Not yet explored.

## Files involved
- `shelfcloud-decky/main.py` — `install_controller_layout()`, `_register_configset()`, `_download_custom_layout()`
- `shelfcloud-decky/src/lib/sync.ts` — `applyControllerConfig()`, `deckControllerIndex()`
- `shelfcloud/backend/data/controller_layouts/` — custom VDF files served by the backend
- `shelfcloud/backend/providers.py` — `LAYOUT_BOOSTEROID = "custom://boosteroid-cloud.vdf"`
