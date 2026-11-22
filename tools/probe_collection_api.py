#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Ask the running Steam client how a collection can have apps REMOVED.

Why this exists: collection.ts only ever adds. A tile filed under one provider
stays there after the provider it launches through changes, so a game can sit
in "Amazon Luna (Shelfcloud)" and open GeForce NOW. Fixing that needs a removal
call, and @decky/ui types none of this — the AddApps signature in collection.ts
carries a comment saying it was established by probing, after a guess cost
real artwork.

So this asks rather than guesses. It runs ON the Deck, where Steam's CDP
endpoint listens on 127.0.0.1:8080, and only READS: it lists method names and
property shapes. Nothing is added, removed or saved.

    python3 probe_collection_api.py
"""

import json
import os
import sys

sys.path.insert(0, "/home/deck/homebrew/plugins/Shelfcloud/py_modules")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import cdp  # noqa: E402

PORT = 8080

# Every method name reachable on a collection and on its drag-drop view,
# including the prototype chain — minified builds put them there rather than as
# own properties.
FIND_METHODS = r"""
(() => {
  const out = {};

  const methods = (obj) => {
    const names = new Set();
    for (let o = obj; o && o !== Object.prototype; o = Object.getPrototypeOf(o)) {
      for (const key of Object.getOwnPropertyNames(o)) {
        let value;
        try { value = obj[key]; } catch (e) { continue; }
        if (typeof value === "function") names.add(key);
      }
    }
    return [...names].sort();
  };

  try {
    if (typeof collectionStore === "undefined") return JSON.stringify({noStore: true});

    out.storeMethods = methods(collectionStore)
      .filter((n) => /app|collect|remove|delete|save|set/i.test(n));

    // A Shelfcloud collection if one exists, otherwise any user collection:
    // the shape is the same and nothing here is written.
    const all = collectionStore.userCollections || [];
    const mine = all.find((c) => /\(Shelfcloud\)$/.test(c.displayName)) || all[0];
    if (!mine) return JSON.stringify({ ...out, noCollections: true, total: all.length });

    out.sample = mine.displayName;
    out.collectionMethods = methods(mine);
    out.collectionKeys = Object.keys(mine).slice(0, 40);
    out.appCount = (mine.allApps || []).length;
    out.hasVisibleApps = Array.isArray(mine.visibleApps);

    try {
      const dd = mine.AsDragDropCollection();
      out.dragDropMethods = methods(dd);
      out.dragDropKeys = Object.keys(dd).slice(0, 40);
    } catch (e) {
      out.dragDropError = String(e);
    }
  } catch (e) {
    out.error = String(e);
  }
  return JSON.stringify(out);
})()
"""


def main() -> int:
    targets = cdp.list_targets(PORT, timeout=6)
    shared = next((t for t in targets if t.get("title") == "SharedJSContext"), None)
    if not shared:
        print("SharedJSContext not found. Is Steam running with CDP on 8080?")
        print("targets:", [t.get("title") for t in targets])
        return 1

    raw = cdp.evaluate(shared["webSocketDebuggerUrl"], FIND_METHODS, timeout=20)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        print("unparseable answer:", raw)
        return 1

    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
