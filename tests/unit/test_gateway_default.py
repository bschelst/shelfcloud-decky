# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""The default gateway address, which is written down twice.

main.py has DEFAULT_GATEWAY and index.tsx has GATEWAY_URL. They must agree:
the backend uses its copy for every request, the panel uses its copy to show
the user where they are pointed, and a mismatch means the panel confidently
displays an address the plugin is not talking to.

The bug that prompted this was a single letter — "selfcloud" for "shelfcloud"
— in both files. Nothing caught it, because nothing exercises the default: a
developer's plugin has an explicit gateway_url in its settings, so the constant
is only ever reached by an install that has never been configured. That is
precisely a first-time user, and for them the plugin would fail to resolve the
host with no clue why.

Run: python3 -m pytest tests/unit
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def backend_default():
    match = re.search(r'^DEFAULT_GATEWAY\s*=\s*"([^"]+)"', read("main.py"), re.M)
    assert match, "main.py no longer defines DEFAULT_GATEWAY"
    return match.group(1)


def frontend_default():
    match = re.search(r'^const GATEWAY_URL\s*=\s*"([^"]+)"', read("src", "index.tsx"), re.M)
    assert match, "index.tsx no longer defines GATEWAY_URL"
    return match.group(1)


def test_the_two_copies_agree():
    """The whole point. Either file alone can be wrong and look fine."""
    assert backend_default() == frontend_default()


def test_the_default_is_https():
    """The gateway holds the bearer token. Plain HTTP would put it on the wire,
    and Steam's client blocks mixed content besides."""
    assert backend_default().startswith("https://")


def test_the_host_is_spelled_shelfcloud():
    """The actual bug: 'selfcloud' is a plausible-looking typo that resolves to
    nothing, and reads as correct at a glance."""
    host = backend_default().split("//", 1)[1].split("/", 1)[0]
    assert host.startswith("shelfcloud."), f"suspicious gateway host: {host}"
    assert "selfcloud" not in backend_default()


def test_no_trailing_slash():
    """Paths are appended directly, so a trailing slash yields '//api/...'."""
    assert not backend_default().endswith("/")
