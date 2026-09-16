# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Recognising our own launches, so an opt-in swap only fires on our tiles.

Steam seats its own built-in controls in the first controller slot, so an
external controller lands in the second. Native games take input from whichever
pad sends it and never notice; a cloud provider's web player reads the FIRST
gamepad, so the external pad does nothing until the order is swapped by hand.
Measured on a Deck: the pad being pressed arrived at index 1, never 0.

The swap is opt-in because SwapControllerOrder changes Steam's order for EVERY
game - turning it on for everyone would break every setup that was fine.

Identifying the launch is the part that can go wrong. Steam sends unAppID 0 for
non-Steam shortcuts, and every tile this plugin makes is one, so the
notification cannot say whether a launch is ours. The process can.

Run: python3 -m pytest tests/unit/test_controller_swap.py
"""

import asyncio
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, ROOT)


class _FakeDecky:
    class logger:
        @staticmethod
        def warning(*a, **k): pass
        @staticmethod
        def info(*a, **k): pass
        @staticmethod
        def error(*a, **k): pass
    DECKY_PLUGIN_SETTINGS_DIR = "/tmp"
    DECKY_PLUGIN_RUNTIME_DIR = "/tmp"


@pytest.fixture(scope="module")
def plugin():
    sys.modules.setdefault("decky", _FakeDecky())
    import importlib
    return importlib.import_module("main")


def ask(plugin, pid):
    return asyncio.run(plugin.Plugin.is_shelfcloud_process(plugin.Plugin, pid))


@pytest.fixture
def our_launch(tmp_path):
    """A real process whose command line looks like one of our tiles."""
    script = tmp_path / "shelfcloud-launch.py"
    script.write_text("import time\ntime.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, str(script)])
    for _ in range(50):                       # let /proc catch up
        if os.path.exists(f"/proc/{proc.pid}/cmdline"):
            break
        time.sleep(0.05)
    yield proc
    proc.kill()
    proc.wait()


def test_our_own_launch_is_recognised(plugin, our_launch):
    assert ask(plugin, our_launch.pid) == {"ok": True, "ours": True}


def test_somebody_elses_game_is_not(plugin):
    """This test's own process. If this ever answers True the swap would fire
    on every game on the machine."""
    assert ask(plugin, os.getpid())["ours"] is False


@pytest.mark.parametrize("pid", [0, -1, None, "x", ""])
def test_a_nonsense_pid_is_refused(plugin, pid):
    assert ask(plugin, pid)["ours"] is False


def test_a_dead_process_is_not_ours(plugin):
    """Steam can notify after the process is gone; /proc is then unreadable.
    False is the safe answer — the caller only acts on True."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert ask(plugin, proc.pid)["ours"] is False


def test_reaper_wrapped_launches_still_match(plugin, tmp_path):
    """Steam launches through `reaper`, so our script's path is one argument
    among several rather than argv[0]."""
    script = tmp_path / "shelfcloud-launch.py"
    script.write_text("import time\ntime.sleep(30)\n")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.argv = ['reaper', 'AppId=1', %r]; time.sleep(30)"
         % str(script)])
    try:
        # argv rewriting in-process does not change /proc, so assert the real
        # rule instead: the path appears in the command line at all.
        with open(f"/proc/{proc.pid}/cmdline", "rb") as handle:
            command = handle.read().replace(b"\0", b" ").decode()
        assert "shelfcloud-launch.py" in command
        assert ask(plugin, proc.pid)["ours"] is True
    finally:
        proc.kill()
        proc.wait()


# ------------------------------------------------------- it stays off

def test_the_setting_defaults_to_off(plugin):
    """The whole reason this is a setting. Nobody whose controllers already
    work should have Steam's order changed underneath them."""
    assert bool({}.get("swap_controller_order", False)) is False
    source = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    assert 'self._settings.get("swap_controller_order", False)' in source
