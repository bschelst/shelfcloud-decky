#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Handing games back after a cancelled bulk add.

"Add all my games" adds every owned game in ONE server call and then builds
tiles one at a time, so cancelling stopped the tiles and left the library
holding hundreds of games that appeared on the next sync anyway. Cancel now
means "stop, and forget the rest", and this is the call that does the forgetting.

Two things are asserted rather than assumed, because both fail quietly:

  * the gateway refuses a change carrying more than 500 entries, and a
    cancelled bulk add is routinely larger than that — unbatched, the whole
    call fails and the library keeps every game
  * a failure here must not fail the sync. The tiles are already correct; this
    is tidying the library behind them.

Run: python3 -m pytest tests/unit
"""

import asyncio
import os
import sys
import types
from unittest import mock

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = "/plugins/Shelfcloud"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/settings/Shelfcloud"
decky.logger = mock.MagicMock()
decky.emit = mock.AsyncMock()
decky.DECKY_PLUGIN_VERSION = "0.0.0-test"
sys.modules.setdefault("decky", decky)

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402


def plugin(sent, fail=False):
    instance = main.Plugin()
    instance._settings = {}
    instance._synced = {}

    def request(path, method="GET", body=None):
        sent.append((path, method, body))
        if fail:
            raise RuntimeError("gateway said no")
        return {"added": 0, "removed": len((body or {}).get("remove", []))}

    instance._request = request
    return instance


def test_one_batch_goes_in_one_call():
    sent = []
    result = asyncio.run(plugin(sent).drop_from_library([1, 2, 3]))

    assert result == {"ok": True, "removed": 3}
    assert len(sent) == 1
    path, method, body = sent[0]
    assert (path, method) == ("/api/me/library", "POST")
    assert body == {"remove": [1, 2, 3]}


def test_a_large_run_is_split_under_the_cap():
    """The gateway refuses more than 500 in one change. A cancelled add of a
    three-hundred-game library is ordinary, and of a thousand is not rare."""
    sent = []
    ids = list(range(1000))
    result = asyncio.run(plugin(sent).drop_from_library(ids))

    assert result["ok"] is True
    assert result["removed"] == 1000
    assert len(sent) > 1
    for _, _, body in sent:
        assert len(body["remove"]) <= 500, "a batch would be refused whole"
    # Every id goes exactly once — a batching slip that dropped or repeated a
    # slice would still look like success.
    together = [i for _, _, body in sent for i in body["remove"]]
    assert together == ids


def test_nothing_to_drop_makes_no_call():
    sent = []
    assert asyncio.run(plugin(sent).drop_from_library([])) == {"ok": True, "removed": 0}
    assert sent == []


def test_rubbish_ids_are_dropped_and_real_ones_kept():
    """Via as_id, so the shapes JSON actually produces all arrive: an int, a
    float from a language whose numbers are all floats, a string from a proxy
    that rewrote the body. True is rejected — it is a subclass of int and would
    otherwise become the id 1, which is a real game."""
    sent = []
    asyncio.run(plugin(sent).drop_from_library([1, None, "2", {}, "x", 3.0, True, 4.5]))
    assert sent[0][2] == {"remove": [1, 2, 3]}


def test_a_failure_is_reported_never_raised():
    """This runs after the tiles are already right. Raising here would fail a
    sync that actually succeeded."""
    sent = []
    result = asyncio.run(plugin(sent, fail=True).drop_from_library([1, 2]))
    assert result["ok"] is False
    assert "gateway said no" in result["error"]


def test_a_failure_part_way_reports_what_did_go():
    """So the count shown to the person matches what actually happened."""
    sent = []
    instance = main.Plugin()
    instance._settings = {}
    instance._synced = {}

    def request(path, method="GET", body=None):
        sent.append(body)
        if len(sent) > 1:
            raise RuntimeError("connection reset")
        return {"removed": len(body["remove"])}

    instance._request = request
    result = asyncio.run(instance.drop_from_library(list(range(500))))

    assert result["ok"] is False
    assert result["removed"] == main._LIBRARY_BATCH
