# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Dry-run the synced.json re-key against a real state file.

The migration runs once, on load, against a file that holds every tile the
plugin has ever made. Getting it wrong loses the app ids, and a lost app id is
a tile recreated from scratch — new playtime, new artwork, new controller
config. So it gets rehearsed on a copy first.

    python3 tools/check_rekey.py ~/synced.json.before-rekey
"""

import json
import os
import sys
import types
from collections import Counter
from unittest import mock

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = "/plugins/Shelfcloud"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/settings/Shelfcloud"
decky.logger = mock.MagicMock()
decky.emit = mock.AsyncMock()
decky.DECKY_PLUGIN_VERSION = "0.0.0-check"
sys.modules["decky"] = decky

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from main import Plugin  # noqa: E402


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "synced.json"
    with open(path, encoding="utf-8") as handle:
        before = json.load(handle)

    after = Plugin._migrate_keys(before)

    print(f"{path}\n  entries before: {len(before)}\n  entries after:  {len(after)}")

    problems = []
    if len(after) != len(before):
        problems.append(f"entry count changed: {len(before)} -> {len(after)}")

    # Every app id must survive, and none may be duplicated onto one key.
    ids_before = sorted(e["app_id"] for e in before.values()
                        if isinstance(e, dict) and "app_id" in e)
    ids_after = sorted(e["app_id"] for e in after.values()
                       if isinstance(e, dict) and "app_id" in e)
    if ids_before != ids_after:
        problems.append("the set of app ids changed")

    if Plugin._migrate_keys(after) != after:
        problems.append("running it twice does not give the same answer")

    bare = [k for k in after if ":" not in k]
    print(f"  re-keyed:       {len(after) - len(bare)}")
    print(f"  left bare:      {len(bare)} (no provider recorded)")
    print("  by provider:    " + ", ".join(
        f"{p}={n}" for p, n in Counter(
            k.split(":", 1)[1] for k in after if ":" in k).most_common()))

    if problems:
        for problem in problems:
            print("  PROBLEM:", problem)
        return 1
    print("  OK - nothing lost, and it is idempotent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
