# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Where a website tile is allowed to go.

Two providers carry pages, not streams — `shelfcloud` and `custom` — and can't
be held to ALLOWED_LAUNCH_DOMAINS: going somewhere new is the whole feature.
This one file holds the rule, because main.py and the launcher (twice) all need
the same answer. The gateway enforces it too in backend/websites.py, but a
server can be compromised; this is what makes it safe.
"""

import ipaddress
import urllib.parse

# Providers whose tiles open a page. Their URLs use the rule below; everything
# else on this device still uses the five-host provider allowlist.
WEBSITE_PROVIDERS = ("shelfcloud", "custom")

# Names that mean this machine or the network it is on. A tile opens in the
# person's own browser with their own cookies, so "http://192.168.1.1/admin" is
# not a website, it is a request to their own router.
_PRIVATE_SUFFIXES = (".local", ".internal", ".localdomain", ".home", ".lan")


def _host_is_public(host: str) -> bool:
    if not host:
        return False
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(_PRIVATE_SUFFIXES):
        return False
    try:
        address = ipaddress.ip_address(lowered.strip("[]"))
    except ValueError:
        pass
    else:
        return not (address.is_private or address.is_loopback or address.is_link_local
                    or address.is_reserved or address.is_multicast
                    or address.is_unspecified)

    # A name, then. We judge it by shape, because looking it up here could give
    # a different answer than the browser gets a moment later. The last label
    # must have a letter, or "0x7f.0.0.1", "0177.0.0.1" and "127.1" all pass
    # here while Chromium still sends them to 127.0.0.1.
    if "." not in lowered or "_" in lowered:
        return False
    return any(c.isalpha() for c in lowered.rsplit(".", 1)[-1])


def is_allowed_website_url(url) -> bool:
    """Is this somewhere a website tile may open?

    Any public http or https. http is fine: a page opens in its own browser
    profile with no provider session, so there is no login on the wire to
    protect. We refuse anything inside this network, any login hidden in the
    authority, and any character a browser moves before it looks up the host.

    That last one: urlsplit follows RFC 3986, where a backslash is a normal
    character, but Chromium follows WHATWG, where it ends the authority like a
    slash. So `https://evil.example\\cloud.boosteroid.com/steal` has one host
    here and another in the browser, and the browser is the one that opens it.
    Tab, CR and LF move the same boundary.
    """
    if not url or not isinstance(url, str):
        return False
    # Check the raw string first. .strip() would drop a trailing newline that
    # is itself the attack.
    if any(c in url for c in "\\\t\r\n"):
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.username or parts.password:
        return False
    return _host_is_public((parts.hostname or "").lower())
