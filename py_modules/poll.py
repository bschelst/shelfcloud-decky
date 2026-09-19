# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Poll timing constants and jitter for the background sync loop."""

import random

SYNC_INTERVAL_CHOICES = [3, 5, 10, 30, 60]
DEFAULT_SYNC_MINUTES = 5
MIN_SYNC_SECONDS = 3 * 60
SYNC_JITTER = 0.15
STARTUP_SETTLE_SECONDS = 20
STARTUP_SPREAD_MAX_SECONDS = 60


def next_poll_delay(base_seconds: float, rng=None) -> float:
    """Seconds to wait before the next poll, jittered around `base_seconds`.

    Pure and separate from the loop so it can be tested: a jitter bug is
    invisible in a single-device test — one client polling on a slightly wrong
    schedule looks exactly like one polling correctly — and only shows up as a
    thundering herd once there are many.
    """
    if rng is None:
        rng = random
    spread = base_seconds * SYNC_JITTER
    return max(MIN_SYNC_SECONDS, base_seconds + rng.uniform(-spread, spread))
