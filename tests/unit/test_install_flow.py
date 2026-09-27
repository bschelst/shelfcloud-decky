# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The install state machine in main.py.

The panel renders straight from this state: a `running` never cleared is a
forever spinner, a failure not recorded is a button pressed again.
"""

import asyncio
import os
import subprocess
import sys
import types
from unittest import mock

decky = types.ModuleType("decky")
decky.DECKY_PLUGIN_DIR = "/plugins/Shelfcloud"
decky.DECKY_PLUGIN_SETTINGS_DIR = "/settings/Shelfcloud"
decky.logger = mock.MagicMock()
decky.emit = mock.AsyncMock()
decky.DECKY_PLUGIN_VERSION = "0.0.0-test"
sys.modules["decky"] = decky

helpers = types.ModuleType("helpers")
helpers.get_ssl_context = lambda: None
sys.modules.setdefault("helpers", helpers)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402

EDGE = "com.microsoft.Edge"


def completed(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=code,
                                       stdout=stdout, stderr=stderr)


def plugin():
    p = main.Plugin()
    p._settings = {}
    return p


def run(coro):
    return asyncio.run(coro)


async def install_and_wait(p, app_id, run_result, present=True):
    """Start an install with subprocess.run stubbed, and wait for it.

    Calls past the end of `run_result` return success. _run_install makes a
    variable number of subprocess calls, and a side_effect list that runs out
    raises StopIteration in a coroutine, which just hangs the suite (seen on
    3.11, which is what Decky ships).
    """
    remaining = list(run_result)

    def answer(*args, **kwargs):
        return remaining.pop(0) if remaining else completed(0)

    with mock.patch.object(main.subprocess, "run", side_effect=answer), \
         mock.patch.object(main.Plugin, "_is_present", staticmethod(lambda _: present)):
        started = await p.install_browser(app_id)
        if p._install_task:
            await p._install_task
    return started


# --------------------------------------------------------------- refusals

def test_an_unknown_browser_is_refused():
    p = plugin()
    result = run(p.install_browser("com.brave.Browser"))
    assert result["ok"] is False
    assert p._install["running"] is False
    assert p._install_task is None          # nothing was started


def test_a_hostile_app_id_is_refused():
    p = plugin()
    for app_id in ("; rm -rf /", "com.microsoft.Edge; id", "", None):
        result = run(p.install_browser(app_id))
        assert result["ok"] is False, repr(app_id)


def test_a_second_install_is_refused_while_one_runs():
    async def scenario():
        p = plugin()
        p._install = {"running": True, "app_id": EDGE, "label": "Microsoft Edge",
                      "message": "…", "error": None, "done": False}
        return await p.install_browser("com.google.Chrome")
    result = run(scenario())
    assert result["ok"] is False
    assert "already running" in result["error"]


# ---------------------------------------------------------------- success

def test_a_successful_install_reports_done_and_stops_running():
    async def scenario():
        p = plugin()
        await install_and_wait(p, EDGE, [completed(0), completed(0)], present=True)
        return p._install
    state = run(scenario())
    assert state["running"] is False
    assert state["done"] is True
    assert state["error"] is None
    assert "Microsoft Edge" in state["message"]


def test_the_remote_is_ensured_before_installing():
    async def scenario():
        p = plugin()
        calls = []

        def record(argv, **kwargs):
            calls.append(argv)
            return completed(0)

        with mock.patch.object(main.subprocess, "run", side_effect=record), \
             mock.patch.object(main.Plugin, "_is_present", staticmethod(lambda _: True)):
            await p.install_browser(EDGE)
            await p._install_task
        return calls
    calls = run(scenario())

    # assert the order, not the command count: a pinned count breaks when an
    # unrelated step is added, which says nothing about ordering
    flat = [" ".join(argv) for argv in calls]
    remote_add = next(i for i, c in enumerate(flat) if "remote-add" in c)
    install = next(i for i, c in enumerate(flat) if " install " in f" {c} ")
    assert remote_add < install, f"installed before the remote existed: {flat}"


# ---------------------------------------------------------------- failures

def test_a_failing_install_records_the_reason():
    async def scenario():
        p = plugin()
        await install_and_wait(
            p, EDGE,
            [completed(0), completed(1, stderr="error: Unable to load summary\nnetwork unreachable")],
        )
        return p._install
    state = run(scenario())
    assert state["running"] is False
    assert state["done"] is True
    assert "network unreachable" in state["error"]


def test_a_silent_failure_is_caught():
    """flatpak has exited 0 with nothing installed. Believe the disk."""
    async def scenario():
        p = plugin()
        await install_and_wait(p, EDGE, [completed(0), completed(0)], present=False)
        return p._install
    state = run(scenario())
    assert state["error"] is not None
    assert state["running"] is False


def test_a_timeout_ends_the_spinner():
    async def scenario():
        p = plugin()
        with mock.patch.object(main.subprocess, "run",
                               side_effect=[completed(0),
                                            subprocess.TimeoutExpired(cmd="flatpak", timeout=1)]), \
             mock.patch.object(main.Plugin, "_is_present", staticmethod(lambda _: False)):
            await p.install_browser(EDGE)
            await p._install_task
        return p._install
    state = run(scenario())
    assert state["running"] is False
    assert state["done"] is True
    assert "too long" in state["error"]


def test_an_unexpected_crash_still_clears_running():
    """The spinner must never outlive the work, whatever went wrong."""
    async def scenario():
        p = plugin()
        with mock.patch.object(main.subprocess, "run", side_effect=OSError("boom")), \
             mock.patch.object(main.Plugin, "_is_present", staticmethod(lambda _: False)):
            await p.install_browser(EDGE)
            await p._install_task
        return p._install
    state = run(scenario())
    assert state["running"] is False
    assert state["done"] is True
    assert state["error"]


# ------------------------------------------------------------- dismissing

def test_dismiss_clears_a_finished_install():
    async def scenario():
        p = plugin()
        p._install = {"running": False, "app_id": EDGE, "label": "Microsoft Edge",
                      "message": "Could not install", "error": "nope", "done": True}
        with mock.patch.object(main.Plugin, "get_status",
                               mock.AsyncMock(return_value={})):
            await p.dismiss_install()
        return p._install
    state = run(scenario())
    assert state == main.IDLE_INSTALL


def test_dismiss_does_not_interrupt_a_running_install():
    async def scenario():
        p = plugin()
        running = {"running": True, "app_id": EDGE, "label": "Microsoft Edge",
                   "message": "Downloading…", "error": None, "done": False}
        p._install = dict(running)
        with mock.patch.object(main.Plugin, "get_status",
                               mock.AsyncMock(return_value={})):
            await p.dismiss_install()
        return p._install, running
    state, running = run(scenario())
    assert state == running


def test_idle_state_has_every_key_the_panel_reads():
    for key in ("running", "app_id", "label", "message", "error", "done"):
        assert key in main.IDLE_INSTALL, key
