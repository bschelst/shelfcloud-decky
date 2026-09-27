# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The default gateway address, written down twice.

main.py has DEFAULT_GATEWAY and index.tsx has GATEWAY_URL. They must agree, or
the panel shows an address the plugin is not using. Only a fresh, unconfigured
install ever reaches the default, so a typo here bites first-time users.
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
    """The bearer token rides on this, so plain HTTP would leak it."""
    assert backend_default().startswith("https://")


def test_the_host_is_spelled_shelfcloud():
    """The actual bug: 'selfcloud' looks right but resolves to nothing."""
    host = backend_default().split("//", 1)[1].split("/", 1)[0]
    assert host.startswith("shelfcloud."), f"suspicious gateway host: {host}"
    assert "selfcloud" not in backend_default()


def test_no_trailing_slash():
    """Paths are appended directly, so a trailing slash yields '//api/...'."""
    assert not backend_default().endswith("/")
