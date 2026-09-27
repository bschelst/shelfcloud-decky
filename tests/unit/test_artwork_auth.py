# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Artwork through the gateway carries the token, a provider CDN never does.

The proxy URL is built by _artwork_proxy and read by fetch_artwork, so we test
that the two agree instead of assuming it.
"""

import asyncio
import os
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

# Read from the implementation. A stale literal here would make every "no
# token" assertion below pass for the wrong reason.
GATEWAY = main.DEFAULT_GATEWAY
TOKEN = "plugin-token-eeeeeeeeeeeeeeeeeeee"


def plugin(token=TOKEN):
    instance = main.Plugin()
    instance._settings = {"api_token": token}
    return instance


class FakeResponse:
    """Minimal stand-in for urlopen's context manager."""

    def __init__(self, content_type="image/png", body=b"\x89PNG-bytes"):
        self.headers = {"Content-Type": content_type}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, _limit=None):
        return self._body


def fetch(instance, url, response=None):
    """Call fetch_artwork, returning (result, the Request that went out)."""
    sent = {}

    def fake_open(url, headers=None, method="GET", body=None, timeout=None):
        # Signature must match _open exactly. A wrong arg raises inside the try
        # and comes back as {"ok": False}, wich looks like a refused fetch.
        # headers is a dict, not a urllib Request, since we use the shared pool.
        sent["request"] = {"url": url, "headers": dict(headers or {})}
        return response or FakeResponse()

    with mock.patch.object(main, "_open", fake_open):
        result = asyncio.run(instance.fetch_artwork(url))
    return result, sent.get("request")


def header_of(request, name):
    """One header from the captured call, matched case-insensitive."""
    if not request:
        return None
    for key, value in request["headers"].items():
        if key.lower() == name.lower():
            return value
    return None


# --------------------------------------------------- the token goes to us

def test_gateway_artwork_carries_the_token():
    instance = plugin()
    result, request = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule")
    assert result["ok"] is True
    assert header_of(request, "Authorization") == f"Bearer {TOKEN}"


def test_the_proxy_url_the_plugin_builds_is_one_it_will_authenticate():
    """The two halves must agree, or the token stops being attached."""
    instance = plugin()
    urls = instance._artwork_proxy({"id": 12})
    assert urls, "expected proxy urls for a valid game id"
    for slot, url in urls.items():
        _, request = fetch(instance, url)
        assert header_of(request, "Authorization") == f"Bearer {TOKEN}", (
            f"the {slot} proxy URL {url} did not match the gateway base")


def test_the_gateway_base_has_no_trailing_slash():
    """A trailing slash would make the proxy URL "…org//api/artwork/…"."""
    assert not GATEWAY.endswith("/")


# ----------------------------------------------- and nowhere else

def test_a_provider_cdn_never_receives_the_token():
    instance = plugin()
    _, request = fetch(instance, "https://cdn2.steamgriddb.com/thumb/abc.png")
    assert header_of(request, "Authorization") is None


def test_a_lookalike_host_does_not_get_the_token():
    """Prefix match, so the "/" ending the authority matters: a host whose
    name merely begins with ours must not get the credential."""
    instance = plugin()
    for hostile in (f"{GATEWAY}.evil.com/api/artwork/1/capsule",
                    f"{GATEWAY}@evil.com/api/artwork/1/capsule",
                    f"{GATEWAY}evil.com/x.png"):
        _, request = fetch(instance, hostile)
        assert header_of(request, "Authorization") is None, hostile


def test_no_token_means_no_header_rather_than_an_empty_one():
    """Signed out, no header at all -- not "Bearer ", which reads as broken."""
    instance = plugin(token=None)
    _, request = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule")
    assert header_of(request, "Authorization") is None


# ------------------------------------------------------- response validation

def test_a_non_image_response_is_rejected():
    """A 401 arrives as JSON. Without this check it gets handed to Steam as
    artwork and the tile shows corrupt instead of missing."""
    instance = plugin()
    result, _ = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule",
                      response=FakeResponse("application/json", b'{"detail":"x"}'))
    assert result["ok"] is False
    assert "not an image" in result["error"]


def test_a_non_http_url_is_refused_before_any_request():
    instance = plugin()
    for url in ("file:///etc/passwd", "", "ftp://host/x.png"):
        assert asyncio.run(instance.fetch_artwork(url))["ok"] is False
