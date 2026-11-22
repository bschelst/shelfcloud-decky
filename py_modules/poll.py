# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Poll timing constants and jitter for the background sync loop."""

import random

# 0 means "Off": the loop keeps running but never syncs, so it can be turned
# back on without a reload.
SYNC_INTERVAL_CHOICES = [0, 3, 5, 10, 30, 60]
DEFAULT_SYNC_MINUTES = 5
MIN_SYNC_SECONDS = 3 * 60
SYNC_JITTER = 0.15
STARTUP_SETTLE_SECONDS = 20
STARTUP_SPREAD_MAX_SECONDS = 60


def next_poll_delay(base_seconds: float, rng=None) -> float:
    """Seconds to wait before the next poll, jittered around `base_seconds`.

    Kept apart from the loop so it can be tested. a jitter bug is invisible on
    one device and only shows up as a thundering herd once there are many.
    """
    if rng is None:
        rng = random
    spread = base_seconds * SYNC_JITTER
    return max(MIN_SYNC_SECONDS, base_seconds + rng.uniform(-spread, spread))
