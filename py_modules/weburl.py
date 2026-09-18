# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Where a website tile is allowed to go.

Two providers carry pages rather than streams — `shelfcloud` for pages the
service publishes, `custom` for pages somebody added for themselves. Neither
can be held to ALLOWED_LAUNCH_DOMAINS: going somewhere the catalogue has never
heard of is the entire feature.

This module holds the rule that replaces it, in one file, because three places
need the same answer and a disagreement between any two of them is a hole:

  * main.py, deciding whether an entry becomes a Steam shortcut at all
  * the launcher, checking what the gateway just said
  * the launcher again, checking the link that has been sat on disk since

The gateway applies the same rule in backend/websites.py. That one is not what
makes this safe — a gateway is a server and servers get compromised. This is.
"""

import ipaddress
import urllib.parse

# Providers whose tiles open a page. Their URLs use the rule below; everything
# else on this device still uses the five-host provider allowlist.
WEBSITE_PROVIDERS = ("shelfcloud", "custom")

# Names that mean "somewhere on the machine, or on the network it sits in".
#
# A tile is opened by the person's own browser, inside their own network, with
# their own cookies — so "http://192.168.1.1/admin" is not a website, it is a
# request to their router, made by whatever address the gateway handed over.
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

    # A name, then — judged by shape, since resolving it here would be a
    # different answer from the one the browser gets a moment later.
    #
    # The last label must contain a letter. Without that, "0x7f.0.0.1",
    # "0177.0.0.1" and "127.1" all pass: ipaddress refuses to read them, and
    # Chromium resolves every one to 127.0.0.1.
    if "." not in lowered or "_" in lowered:
        return False
    return any(c.isalpha() for c in lowered.rsplit(".", 1)[-1])


def is_allowed_website_url(url) -> bool:
    """Is this somewhere a website tile may open?

    Any public http or https address. http is permitted because a page is not
    a stream: it opens in its own browser profile holding none of the provider
    sessions, so there is no credential on the wire to protect. What is refused
    is anything inside this network, anything with credentials hidden in the
    authority, and anything containing a character a browser moves before it
    resolves the host.

    That last one is the same disagreement is_allowed_launch_url documents:
    urlsplit follows RFC 3986, where a backslash is an ordinary character, and
    Chromium follows WHATWG, where it ends the authority like a slash. So

        https://evil.example\\cloud.boosteroid.com/steal

    has one host here and a different host in the browser, and the browser is
    the one that opens the page. Tab, CR and LF move the boundary the same way.
    """
    if not url or not isinstance(url, str):
        return False
    # On the RAW string, before stripping: .strip() would remove a trailing
    # newline that is itself the attack.
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
