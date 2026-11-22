# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""How long a player count, and a failed one, may be reused.

A count and a failure are not the same kind of fact. A count is context and can
be reused for fifteen minutes; a failure is about right now, so it is cached
only briefly, or a minute of bad connectivity leaves a dash for the full window.
"""

import asyncio
import sys

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main

APPID = "730"


def plugin():
    instance = main.Plugin()
    instance._settings = {}
    instance._players_cache = {}
    return instance


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _answering(value):
    """A _fetch_players that returns `value` and counts its calls."""
    calls = []

    def fetch(_cls_or_id, *rest):
        calls.append(1)
        if isinstance(value, Exception):
            raise value
        return value
    return fetch, calls


def test_a_good_count_is_reused(monkeypatch):
    instance = plugin()
    fetch, calls = _answering({"ok": True, "players": 1161591})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))

    first = run(instance.current_players(APPID))
    second = run(instance.current_players(APPID))

    assert first == second == {"ok": True, "players": 1161591}
    assert len(calls) == 1, "asked Steam twice for the same game"


def test_a_good_count_is_reused_for_the_full_fifteen_minutes(monkeypatch):
    instance = plugin()
    fetch, calls = _answering({"ok": True, "players": 10})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))

    run(instance.current_players(APPID))
    # Fourteen minutes later it is still good
    stamp, value = instance._players_cache[APPID]
    instance._players_cache[APPID] = (stamp - 14 * 60, value)
    run(instance.current_players(APPID))

    assert len(calls) == 1


def test_a_good_count_is_asked_again_after_fifteen_minutes(monkeypatch):
    instance = plugin()
    fetch, calls = _answering({"ok": True, "players": 10})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))

    run(instance.current_players(APPID))
    stamp, value = instance._players_cache[APPID]
    instance._players_cache[APPID] = (stamp - 16 * 60, value)
    run(instance.current_players(APPID))

    assert len(calls) == 2


def test_a_failure_is_remembered_briefly(monkeypatch):
    """Still cached: an appid Steam refuses outright must not be re-asked on
    every page open."""
    instance = plugin()
    fetch, calls = _answering(RuntimeError("no route to host"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))

    first = run(instance.current_players(APPID))
    run(instance.current_players(APPID))

    assert first["ok"] is False
    assert len(calls) == 1


def test_a_failure_does_not_outlast_the_outage(monkeypatch):
    """The point of splitting them. A minute of bad conectivity used to leave
    a dash on that game for the full fifteen minutes."""
    instance = plugin()
    fetch, calls = _answering(RuntimeError("no route to host"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))
    run(instance.current_players(APPID))

    # Two minutes later, with the network back.
    stamp, value = instance._players_cache[APPID]
    instance._players_cache[APPID] = (stamp - 120, value)
    good, _ = _answering({"ok": True, "players": 42})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(good))

    assert run(instance.current_players(APPID)) == {"ok": True, "players": 42}
    assert len(calls) == 1, "the failed call should not have been repeated yet"


def test_a_failure_is_still_cached_within_the_minute(monkeypatch):
    instance = plugin()
    fetch, calls = _answering(RuntimeError("delisted"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))
    run(instance.current_players(APPID))

    stamp, value = instance._players_cache[APPID]
    instance._players_cache[APPID] = (stamp - 30, value)
    run(instance.current_players(APPID))

    assert len(calls) == 1


def test_something_that_is_not_an_appid_never_reaches_steam(monkeypatch):
    """About one game in six is not on Steam at all."""
    instance = plugin()
    fetch, calls = _answering({"ok": True, "players": 1})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))

    for bad in ("", "not-a-number", "12a", None, 730):
        assert run(instance.current_players(bad))["ok"] is False
    assert calls == []


def _expire(instance, seconds):
    stamp, value = instance._players_cache[APPID]
    instance._players_cache[APPID] = (stamp - seconds, value)


def test_a_failure_after_a_good_count_keeps_showing_that_count(monkeypatch):
    """A blip at Steam used to put a dash on the page for a minute, while the
    number from a moment ago was right there. Now that number stays."""
    instance = plugin()
    good, calls = _answering({"ok": True, "players": 54946})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(good))
    run(instance.current_players(APPID))

    _expire(instance, 16 * 60)
    bad, _ = _answering(RuntimeError("timed out"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(bad))
    answer = run(instance.current_players(APPID))

    assert answer["ok"] is True
    assert answer["players"] == 54946
    assert answer.get("stale") is True


def test_the_kept_count_is_also_used_within_the_failure_minute(monkeypatch):
    instance = plugin()
    good, _ = _answering({"ok": True, "players": 7})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(good))
    run(instance.current_players(APPID))
    _expire(instance, 16 * 60)
    bad, calls = _answering(RuntimeError("timed out"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(bad))
    run(instance.current_players(APPID))

    again = run(instance.current_players(APPID))
    assert again["players"] == 7
    assert len(calls) == 1, "the failure minute should still hold Steam off"


def test_a_count_older_than_two_hours_is_not_shown_as_current(monkeypatch):
    instance = plugin()
    good, _ = _answering({"ok": True, "players": 7})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(good))
    run(instance.current_players(APPID))
    instance._players_good[APPID] = (instance._players_good[APPID][0] - 3 * 3600,
                                     instance._players_good[APPID][1])
    _expire(instance, 16 * 60)
    bad, _ = _answering(RuntimeError("timed out"))
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(bad))

    assert run(instance.current_players(APPID))["ok"] is False


def test_clearing_forgets_counts_and_fallbacks(monkeypatch):
    """The Clear cache button: the next ask goes to Steam, and no old count
    can come back as a fallback."""
    instance = plugin()
    fetch, calls = _answering({"ok": True, "players": 9})
    monkeypatch.setattr(main.Plugin, "_fetch_players", staticmethod(fetch))
    run(instance.current_players(APPID))

    assert run(instance.clear_player_cache()) == {"ok": True}
    assert instance._players_cache == {} and instance._players_good == {}
    run(instance.current_players(APPID))
    assert len(calls) == 2
