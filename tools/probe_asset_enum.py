#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Read Steam's real ELibraryAssetType out of the running client.

Why this exists: sync.ts declares the asset-type values by hand, with a comment
admitting they are inferred, because @decky/ui does not export the enum. Four
of the five are proven by working artwork. The fifth, Icon, was written on the
strength of the guess alone and artwork disappeared from tiles that had it.

Guessing again is not an option, so this asks the client. It runs ON the Deck,
where Steam's CDP endpoint listens on 127.0.0.1:8080, and only reads — it
evaluates expressions and prints what it finds. Nothing is written here.

    python3 probe_asset_enum.py
"""

import json
import os
import sys

sys.path.insert(0, "/home/deck/homebrew/plugins/Shelfcloud/py_modules")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import cdp  # noqa: E402

PORT = 8080

# Scan only modules webpack has ALREADY instantiated (its cache), never the
# full factory list: calling every factory would execute Steam's own modules
# for their side effects, on a live client the user is holding.
FIND_ENUM = r"""
(() => {
  const found = {};

  const note = (where, value) => {
    if (value && typeof value === "object") found[where] = value;
  };

  // A plain global, if Steam happens to expose one.
  try { note("global", window.ELibraryAssetType); } catch (e) {}

  // Steam's UI is a webpack bundle. Push a no-op chunk to get a handle on
  // __webpack_require__, then read its module cache.
  let req = null;
  try {
    req = window.webpackChunksteamui &&
          window.webpackChunksteamui.push([[Math.random()], {}, (r) => r]);
  } catch (e) { found.pushError = String(e); }

  const cache = req && (req.c || req.cache);
  if (cache) {
    let scanned = 0;
    for (const id in cache) {
      const mod = cache[id] && cache[id].exports;
      if (!mod || typeof mod !== "object") continue;
      scanned++;
      for (const key in mod) {
        if (key !== "ELibraryAssetType") continue;
        note("webpack:" + id, mod[key]);
      }
    }
    found.modulesScanned = scanned;
  } else {
    found.cacheMissing = true;
  }

  return JSON.stringify(found);
})()
"""

# An enum may be minified past its name. This looks for the SHAPE instead: an
# object whose keys are exactly the five slot names we care about.
FIND_BY_SHAPE = r"""
(() => {
  const want = ["Capsule", "Hero", "Logo", "Header", "Icon"];
  const hits = [];
  let req = null;
  try {
    req = window.webpackChunksteamui &&
          window.webpackChunksteamui.push([[Math.random()], {}, (r) => r]);
  } catch (e) { return JSON.stringify({error: String(e)}); }

  const cache = req && (req.c || req.cache);
  if (!cache) return JSON.stringify({cacheMissing: true});

  for (const id in cache) {
    const mod = cache[id] && cache[id].exports;
    if (!mod || typeof mod !== "object") continue;
    for (const key in mod) {
      let value;
      try { value = mod[key]; } catch (e) { continue; }
      if (!value || typeof value !== "object") continue;
      const keys = Object.keys(value);
      const matched = want.filter((w) => keys.includes(w));
      // Needs most of the slot names, not just one, or every object with a
      // "Header" property matches.
      if (matched.length >= 4) {
        hits.push({module: id, exportName: key, value: value});
      }
    }
  }
  return JSON.stringify({hits: hits});
})()
"""


def run(label, expression):
    targets = cdp.list_targets(PORT, timeout=6)
    shared = next((t for t in targets if t.get("title") == "SharedJSContext"), None)
    if not shared:
        print(f"  {label}: SharedJSContext not found")
        print("  targets:", [t.get("title") for t in targets])
        return None
    try:
        raw = cdp.evaluate(shared["webSocketDebuggerUrl"], expression, timeout=25)
    except Exception as exc:
        print(f"  {label}: evaluate failed: {exc}")
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return raw


print("=== looking for ELibraryAssetType by name ===")
by_name = run("by name", FIND_ENUM)
print(json.dumps(by_name, indent=2)[:2000])

print("\n=== looking for it by shape (in case the name is minified away) ===")
by_shape = run("by shape", FIND_BY_SHAPE)
print(json.dumps(by_shape, indent=2)[:2000])
