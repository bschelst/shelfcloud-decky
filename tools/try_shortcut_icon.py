#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Try SetShortcutIcon on exactly ONE shortcut, and check nothing else breaks.

Background: the icon was first attempted as library asset type 4 via
SetCustomArtworkForApp. That value was inferred, not verified, and artwork
vanished from tiles afterwards. Probing the live client showed why — Steam has
a separate call for this:

    SetCustomArtworkForApp   the four library slots
    SetShortcutIcon          shortcut icons, stored in shortcuts.vdf as a PATH

So this writes an icon file to disk and passes its path. It touches one
shortcut, prints what it did, and re-reads the app afterwards so the other four
slots can be confirmed intact before anything ships.

Run ON the Deck:  python3 try_shortcut_icon.py
"""

import json
import os
import sys
import urllib.request

sys.path.insert(0, "/home/deck/homebrew/plugins/Shelfcloud/py_modules")
import cdp  # noqa: E402

SETTINGS = "/home/deck/homebrew/settings/Shelfcloud"
ICON_DIR = "/home/deck/.local/share/shelfcloud/icons"
PORT = 8080


def shared_context():
    targets = cdp.list_targets(PORT, timeout=6)
    for target in targets:
        if target.get("title") == "SharedJSContext":
            return target["webSocketDebuggerUrl"]
    raise SystemExit(f"SharedJSContext not found: {[t.get('title') for t in targets]}")


def settings():
    with open(os.path.join(SETTINGS, "settings.json")) as handle:
        return json.load(handle)


def tracked():
    with open(os.path.join(SETTINGS, "synced.json")) as handle:
        return json.load(handle)


config = settings()
gateway = (config.get("gateway_url") or "").rstrip("/")
token = config.get("api_token")
entries = tracked()
if not entries:
    raise SystemExit("nothing tracked — run a sync first")

# One shortcut, chosen deterministically so re-runs hit the same tile.
game_id, entry = sorted(entries.items(), key=lambda kv: int(kv[0]))[0]
app_id = entry["app_id"]
name = entry.get("name", "?")
print(f"test subject: {name}  (game_id={game_id}, app_id={app_id})")

# Fetch an image to use as the icon. The logo is the closest thing to one;
# fall back to the capsule.
os.makedirs(ICON_DIR, exist_ok=True)
icon_path = os.path.join(ICON_DIR, f"{game_id}.png")
got = None
for slot in ("logo", "capsule"):
    url = f"{gateway}/api/artwork/{game_id}/{slot}"
    request = urllib.request.Request(url, headers={
        "User-Agent": "Shelfcloud/0.1",
        "Authorization": f"Bearer {token}",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
        with open(icon_path, "wb") as handle:
            handle.write(data)
        got = slot
        break
    except Exception as exc:
        print(f"  {slot}: {exc}")

if not got:
    raise SystemExit("could not fetch any image to use as an icon")
print(f"  wrote {len(open(icon_path,'rb').read())} bytes from the {got} slot -> {icon_path}")

ws = shared_context()

BEFORE = """(() => {
  const o = appStore.GetAppOverviewByAppID(%d);
  return JSON.stringify({
    name: o && o.display_name,
    icon: o && (o.icon_data || o.icon_hash || null),
  });
})()""" % app_id

print("\nbefore:", cdp.evaluate(ws, BEFORE, timeout=20))

SET = """(async () => {
  try {
    await SteamClient.Apps.SetShortcutIcon(%d, "%s");
    return JSON.stringify({ok: true});
  } catch (e) {
    return JSON.stringify({ok: false, error: String(e)});
  }
})()""" % (app_id, icon_path)

print("SetShortcutIcon ->", cdp.evaluate(ws, SET, timeout=25))
print("after: ", cdp.evaluate(ws, BEFORE, timeout=20))

print("\nNow look at that game's tile and its overlay:")
print("  - does it have an icon?")
print("  - is its capsule/header/hero artwork STILL there?")
