# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Poll jitter — spreading devices out so they do not arrive as one wall.

A fixed interval keeps a fleet in lockstep, which hurts most when an artwork
revision bump makes every device re-apply art at once. Tested because a jitter
bug is invisible by hand: it only shows at a scale you cannot reproduce.
"""

import os
import random
import statistics
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

FIVE_MINUTES = 300.0


def delays(base, count=400, seed=7):
    rng = random.Random(seed)
    return [main.next_poll_delay(base, rng) for _ in range(count)]


def test_the_delay_stays_close_to_the_configured_interval():
    """Jitter spreads the load; it must not change what the user chose."""
    sample = delays(FIVE_MINUTES)
    assert statistics.mean(sample) == mock.ANY
    assert abs(statistics.mean(sample) - FIVE_MINUTES) < FIVE_MINUTES * 0.02


def test_every_delay_is_inside_the_jitter_band():
    spread = FIVE_MINUTES * main.SYNC_JITTER
    for delay in delays(FIVE_MINUTES):
        assert FIVE_MINUTES - spread <= delay <= FIVE_MINUTES + spread


def test_the_delays_actually_differ():
    """The whole point. A constant here would pass every other test in this
    file while leaving the fleet perfectly synchronised."""
    sample = delays(FIVE_MINUTES, count=50)
    assert len(set(sample)) > 40


def test_devices_starting_together_end_up_spread_out():
    """Model 200 devices over an hour. Without jitter all 200 land in one
    bucket forever; with it, arrivals spread across the interval."""
    horizon = 3600.0
    buckets = {}
    for device in range(200):
        rng = random.Random(device)
        clock = 0.0
        while clock < horizon:
            clock += main.next_poll_delay(FIVE_MINUTES, rng)
            if clock < horizon:
                buckets[int(clock // 10)] = buckets.get(int(clock // 10), 0) + 1

    busiest = max(buckets.values())
    # Unjittered, all 200 devices land in the same 10-second bucket every tick.
    assert busiest < 60, f"still clustered: {busiest} devices in one 10s window"


# ------------------------------------------------------------------ floors

def test_jitter_can_never_poll_faster_than_the_floor():
    """Jitter must not become a way around the rate limit."""
    for delay in delays(float(main.MIN_SYNC_SECONDS)):
        assert delay >= main.MIN_SYNC_SECONDS


def test_a_short_interval_still_respects_the_floor():
    for delay in delays(10.0):
        assert delay >= main.MIN_SYNC_SECONDS


# ------------------------------------------------------------ first poll

def test_the_startup_spread_is_capped():
    """A fleet should disperse immediately, but a user on a long interval must
    not wait that whole interval for his first sync."""
    assert main.STARTUP_SPREAD_MAX_SECONDS <= 60
    longest = main.STARTUP_SETTLE_SECONDS + main.STARTUP_SPREAD_MAX_SECONDS
    assert longest <= 90


def test_steam_still_gets_time_to_settle_before_the_first_sync():
    """Adding shortcuts while Steam's library is still loading is the orginal
    reason this delay exists; jitter must not erode it."""
    assert main.STARTUP_SETTLE_SECONDS >= 20
