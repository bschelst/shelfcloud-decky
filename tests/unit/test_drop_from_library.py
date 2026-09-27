#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Handing games back after a cancelled bulk add.

drop_from_library forgets the rest. It must batch under the gateway's 500-entry
cap, and a failure here must not fail the sync -- the tiles are already right.
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
    """The gateway refuses more than 500 in one change."""
    sent = []
    ids = list(range(1000))
    result = asyncio.run(plugin(sent).drop_from_library(ids))

    assert result["ok"] is True
    assert result["removed"] == 1000
    assert len(sent) > 1
    for _, _, body in sent:
        assert len(body["remove"]) <= 500, "a batch would be refused whole"
    # every id exactly once: a dropped or repeated slice would still look ok
    together = [i for _, _, body in sent for i in body["remove"]]
    assert together == ids


def test_nothing_to_drop_makes_no_call():
    sent = []
    assert asyncio.run(plugin(sent).drop_from_library([])) == {"ok": True, "removed": 0}
    assert sent == []


def test_rubbish_ids_are_dropped_and_real_ones_kept():
    """as_id accepts the shapes JSON produces (int, float, numeric string) but
    rejects True, which is an int subclass and would become the real id 1."""
    sent = []
    asyncio.run(plugin(sent).drop_from_library([1, None, "2", {}, "x", 3.0, True, 4.5]))
    assert sent[0][2] == {"remove": [1, 2, 3]}


def test_a_failure_is_reported_never_raised():
    """Runs after the tiles are right, so raising would fail a good sync."""
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
