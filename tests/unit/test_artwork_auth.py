# Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
"""Artwork fetched through the gateway must carry the token — and only there.

The gateway's /api/artwork endpoint used to answer anonymously. It was the one
endpoint that had to, because this plugin fetched from it without credentials;
gating it while that was true would have blanked every tile on the Deck with
nothing in the log but a 401 the UI never surfaces.

Two properties keep it working, and both fail silently if they drift:

  1. Requests to our own gateway carry the bearer token.
  2. Requests to a provider's CDN do NOT — SteamGridDB has no business
     receiving a Shelfcloud credential.

Property 1 depends on the proxy URL and the comparison base being built the
same way. They are built by _artwork_proxy and read by fetch_artwork, two
methods far apart in the file, so the agreement between them is tested rather
than assumed.

Run: python3 -m pytest tests/unit
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
sys.modules.setdefault("decky", decky)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

import main  # noqa: E402

GATEWAY = "http://192.168.1.50:8000"
TOKEN = "plugin-token-eeeeeeeeeeeeeeeeeeee"


def plugin(gateway=GATEWAY, token=TOKEN):
    instance = main.Plugin()
    instance._settings = {"gateway_url": gateway, "api_token": token}
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

    def fake_urlopen(request, timeout=None):
        sent["request"] = request
        return response or FakeResponse()

    with mock.patch.object(main.urllib.request, "urlopen", fake_urlopen):
        result = asyncio.run(instance.fetch_artwork(url))
    return result, sent.get("request")


def header_of(request, name):
    # urllib title-cases header names as they are added.
    return request.get_header(name.capitalize()) if request else None


# --------------------------------------------------- the token goes to us

def test_gateway_artwork_carries_the_token():
    instance = plugin()
    result, request = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule")
    assert result["ok"] is True
    assert header_of(request, "Authorization") == f"Bearer {TOKEN}"


def test_the_proxy_url_the_plugin_builds_is_one_it_will_authenticate():
    """The two halves must agree, or every tile silently goes blank.

    _artwork_proxy writes the URL; fetch_artwork decides whether it is ours.
    If either changes how the base is spelled — a trailing slash, a different
    setting — the token stops being attached and the gateway answers 401.
    """
    instance = plugin()
    urls = instance._artwork_proxy({"id": 12})
    assert urls, "expected proxy urls for a valid game id"
    for slot, url in urls.items():
        _, request = fetch(instance, url)
        assert header_of(request, "Authorization") == f"Bearer {TOKEN}", (
            f"the {slot} proxy URL {url} did not match the gateway base")


def test_a_gateway_url_with_a_trailing_slash_setting_still_matches():
    """Users type the gateway address by hand; a trailing slash is expected."""
    instance = plugin(gateway=GATEWAY + "/")
    for url in instance._artwork_proxy({"id": 7}).values():
        _, request = fetch(instance, url)
        assert header_of(request, "Authorization") == f"Bearer {TOKEN}"


# ----------------------------------------------- and nowhere else

def test_a_provider_cdn_never_receives_the_token():
    instance = plugin()
    _, request = fetch(instance, "https://cdn2.steamgriddb.com/thumb/abc.png")
    assert header_of(request, "Authorization") is None


def test_a_lookalike_host_does_not_get_the_token():
    """The check is a prefix match, so the separator matters.

    "http://192.168.1.50:8000.evil.com/" starts with the gateway address as a
    string. Requiring the "/" that ends the authority is what stops a host
    whose name merely begins with ours from being handed the credential.
    """
    instance = plugin()
    for hostile in (f"{GATEWAY}.evil.com/api/artwork/1/capsule",
                    f"{GATEWAY}@evil.com/api/artwork/1/capsule",
                    f"{GATEWAY}evil.com/x.png"):
        _, request = fetch(instance, hostile)
        assert header_of(request, "Authorization") is None, hostile


def test_no_token_means_no_header_rather_than_an_empty_one():
    """Signed out, the request goes out bare and the gateway refuses it.

    That is the right outcome — no art without an account — but it must not go
    out as "Bearer ", which reads as a malformed credential rather than none.
    """
    instance = plugin(token=None)
    _, request = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule")
    assert header_of(request, "Authorization") is None


# ------------------------------------------------------- response validation

def test_a_non_image_response_is_rejected():
    """A 401 from the gated endpoint arrives as JSON, not as an image.

    Without this check the error body would be base64-encoded and handed to
    Steam as artwork, which is how a gating change turns into a corrupted tile
    instead of a missing one.
    """
    instance = plugin()
    result, _ = fetch(instance, f"{GATEWAY}/api/artwork/12/capsule",
                      response=FakeResponse("application/json", b'{"detail":"x"}'))
    assert result["ok"] is False
    assert "not an image" in result["error"]


def test_a_non_http_url_is_refused_before_any_request():
    instance = plugin()
    for url in ("file:///etc/passwd", "", "ftp://host/x.png"):
        assert asyncio.run(instance.fetch_artwork(url))["ok"] is False
