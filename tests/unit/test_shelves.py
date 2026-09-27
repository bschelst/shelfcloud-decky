# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Choosing which shelf this device shows.

A shelf is a named library on the gateway; the Deck only picks which one it
shows. The cases worth testing are a gateway answering with something odd and a
token from before shelves existed — neither may crash the panel.
"""

import asyncio
import os
import sys
import types
from unittest import mock

import pytest

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


def plugin(responses):
    """A plugin whose gateway answers from a {path: payload} map. An Exception
    payload is raised instead of returned, to drive the failure paths."""
    instance = main.Plugin()
    instance._settings = {"gateway_url": "http://gw:8000", "api_token": "t"}

    def fake_request(path, method="GET", body=None, authenticated=True):
        reply = responses.get(path, {})
        if isinstance(reply, Exception):
            raise reply
        return reply

    instance._request = fake_request
    return instance


SHELVES = {"shelves": [{"id": 1, "name": "Handheld", "games": 12},
                       {"id": 2, "name": "Couch", "games": 3}]}
DEVICES = {"devices": [{"id": 7, "name": "Deck", "shelf_id": 1},
                       {"id": 8, "name": "TV", "shelf_id": 2}],
           "this_device": 7}


@pytest.fixture
def record_posts():
    """A plugin whose writes are captured instead of sent. `sent` stays empty
    when no request was made — "it never asked" is stronger than "it errored"
    for everything the plugin should refuse."""
    def build(shelves, devices):
        sent = {}
        instance = plugin({"/api/me/shelves": shelves, "/api/me/devices": devices})
        reads = instance._request

        def record(path, method="GET", body=None, authenticated=True):
            if method == "POST":
                sent["path"], sent["body"] = path, body
                return {"ok": True}
            return reads(path, method, body, authenticated)

        instance._request = record
        return instance, sent

    return build


# ------------------------------------------------------------------ reading

def test_it_reports_the_shelves_and_which_one_this_device_is_on():
    p = plugin({"/api/me/shelves": SHELVES, "/api/me/devices": DEVICES})
    result = asyncio.run(p.get_shelves())
    assert result["ok"] is True
    assert [s["name"] for s in result["shelves"]] == ["Handheld", "Couch"]
    assert result["device_id"] == 7
    assert result["shelf_id"] == 1          # this device, not the other one
    assert result["device_name"] == "Deck"


def test_a_device_on_no_shelf_reports_none_rather_than_guessing():
    """None means the shared games, which is what every library was before
    shelves and what a newly signed-in device gets."""
    devices = {"devices": [{"id": 7, "name": "Deck", "shelf_id": None}], "this_device": 7}
    p = plugin({"/api/me/shelves": SHELVES, "/api/me/devices": devices})
    assert asyncio.run(p.get_shelves())["shelf_id"] is None


def test_it_does_not_report_another_devices_shelf():
    """The obvious way to get this wrong is to take the first device in the
    list. That is the TV as often as not."""
    devices = {"devices": [{"id": 8, "name": "TV", "shelf_id": 2},
                           {"id": 7, "name": "Deck", "shelf_id": 1}],
               "this_device": 7}
    p = plugin({"/api/me/shelves": SHELVES, "/api/me/devices": devices})
    assert asyncio.run(p.get_shelves())["shelf_id"] == 1


# ----------------------------------------------------------------- failures

def test_being_signed_out_is_reported_not_raised():
    p = plugin({"/api/me/shelves": PermissionError("no token")})
    result = asyncio.run(p.get_shelves())
    assert result["ok"] is False
    assert result["signed_in"] is False


def test_a_gateway_error_is_reported_not_raised():
    p = plugin({"/api/me/shelves": OSError("connection refused")})
    result = asyncio.run(p.get_shelves())
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_an_unexpected_response_shape_does_not_crash_the_panel():
    """The gateway is a separate service that can be mid-deploy, behind a proxy
    returning an error page, or simply older than this plugin."""
    for junk in ("not json", [], None, 42):
        p = plugin({"/api/me/shelves": junk, "/api/me/devices": DEVICES})
        assert asyncio.run(p.get_shelves())["ok"] is False


def test_a_device_missing_from_the_list_yields_no_shelf_rather_than_an_error():
    """this_device naming a row that is not present should degrade to the
    shared games, not throw."""
    devices = {"devices": [{"id": 8, "name": "TV", "shelf_id": 2}], "this_device": 7}
    p = plugin({"/api/me/shelves": SHELVES, "/api/me/devices": devices})
    result = asyncio.run(p.get_shelves())
    assert result["ok"] is True
    assert result["shelf_id"] is None


# ------------------------------------------------------------------ writing

def test_setting_a_shelf_targets_this_device(record_posts):
    p, sent = record_posts(SHELVES, DEVICES)
    assert asyncio.run(p.set_shelf(2))["ok"] is True
    assert sent["path"] == "/api/me/devices/7/shelf"     # this device
    assert sent["body"] == {"shelf_id": 2}


def test_choosing_shared_sends_null_not_zero(record_posts):
    """null is how "no shelf" is spelled; 0 is a real id and would 404. The
    panel uses 0 as the dropdown value and converts it back to null here."""
    p, sent = record_posts(SHELVES, DEVICES)
    asyncio.run(p.set_shelf(None))
    assert sent["body"] == {"shelf_id": None}


def test_a_token_from_before_shelves_is_told_to_sign_in_again():
    """Such a token has no device attached, so there is nothing to point at.
    The alternative is a dropdown that accepts a choice and changes nothing."""
    devices = {"devices": [], "this_device": None}
    p = plugin({"/api/me/shelves": SHELVES, "/api/me/devices": devices})
    result = asyncio.run(p.set_shelf(1))
    assert result["ok"] is False
    assert "sign in again" in result["error"].lower()


def test_a_shelf_that_no_longer_exists_is_refused_with_a_reason(record_posts):
    """Deleted from the website while the panel sat open. Refusing locally gives
    a clear reason; sending it gives whatever the gateway says about a bad id."""
    p, sent = record_posts(SHELVES, DEVICES)
    result = asyncio.run(p.set_shelf(99))
    assert result["ok"] is False
    assert "no longer exists" in result["error"]
    assert sent == {}, "a shelf known not to exist was still sent to the gateway"


# --------------------------------------------------------------- validation
#
# Everything below is a gateway response the plugin does not expect: mid-deploy,
# a proxy error page, older than the plugin, or just wrong. None may raise, or
# the exception takes the whole panel down.

def test_a_list_field_that_is_not_a_list_is_rejected_not_iterated():
    """`or []` rescues null and nothing else. A string iterates character by
    character and yields empty; an int raises TypeError. The isinstance check
    catches both."""
    for junk in (42, "oops", {"a": 1}, True):
        p = plugin({"/api/me/shelves": {"shelves": junk}, "/api/me/devices": DEVICES})
        assert asyncio.run(p.get_shelves())["ok"] is False

        p = plugin({"/api/me/shelves": SHELVES,
                    "/api/me/devices": {"devices": junk, "this_device": 7}})
        assert asyncio.run(p.get_shelves())["ok"] is False


def test_ids_that_arrive_as_strings_are_usable():
    """A proxy that re-serialises JSON can turn every number into a string.
    Coercing keeps the picker working; refusing would be a dead panel for a
    response that is merely ugly."""
    shelves = {"shelves": [{"id": "3", "name": "Handheld", "games": "7"}]}
    devices = {"devices": [{"id": "7", "shelf_id": "3"}], "this_device": "7"}
    result = asyncio.run(plugin({"/api/me/shelves": shelves,
                                 "/api/me/devices": devices}).get_shelves())
    assert result["shelves"] == [{"id": 3, "name": "Handheld", "games": 7}]
    assert result["shelf_id"] == 3
    assert result["device_id"] == 7


def test_a_boolean_is_not_an_id():
    """bool subclasses int, so a plain isinstance(x, int) accepts True and
    turns it into the id 1 — which is a real shelf, belonging to someone."""
    assert main.as_id(True) is None
    assert main.as_id(False) is None


def test_a_shelf_with_no_name_keeps_its_place_in_the_list():
    """Dropping it is worse than naming it: if the device points at that shelf,
    the picker gets a selected value matching no option and Steam draws an
    empty control."""
    shelves = {"shelves": [{"id": 4, "games": 2}, {"id": 5, "name": "   "}]}
    result = asyncio.run(plugin({"/api/me/shelves": shelves,
                                 "/api/me/devices": DEVICES}).get_shelves())
    assert [s["name"] for s in result["shelves"]] == ["Shelf 4", "Shelf 5"]


def test_an_entry_with_no_id_is_dropped():
    """An id is the whole of what a selection sends, so a row without one is
    a line that cannot be pressed."""
    shelves = {"shelves": [None, "x", {"name": "no id"}, {"id": 1, "name": "A"}]}
    result = asyncio.run(plugin({"/api/me/shelves": shelves,
                                 "/api/me/devices": DEVICES}).get_shelves())
    assert [s["id"] for s in result["shelves"]] == [1]


def test_an_unreadable_device_id_does_not_match_a_device():
    """Both sides use the same coercion, so an unreadable this_device must not
    match an unreadable row id by both being None — that adopts another
    device's shelf."""
    devices = {"devices": [{"id": {}, "shelf_id": 2}], "this_device": []}
    result = asyncio.run(plugin({"/api/me/shelves": SHELVES,
                                 "/api/me/devices": devices}).get_shelves())
    assert result["ok"] is True
    assert result["device_id"] is None
    assert result["shelf_id"] is None


def test_a_device_id_is_never_interpolated_into_the_url_unchecked(record_posts):
    """The id goes straight into a request path, so a hostile string from the
    response could write its own URL and reach another endpoint with this
    user's bearer token."""
    hostile = "7/../../api/catalog?x="
    devices = {"devices": [{"id": hostile, "shelf_id": 1}], "this_device": hostile}
    p, sent = record_posts(SHELVES, devices)

    result = asyncio.run(p.set_shelf(1))

    assert result["ok"] is False
    assert sent == {}, f"request made to {sent.get('path')!r}"


def test_a_device_id_of_zero_is_a_device(record_posts):
    """`if not device_id` would call it missing and tell a signed-in user to
    sign in again — an error with no action behind it."""
    devices = {"devices": [{"id": 0, "shelf_id": None}], "this_device": 0}
    p, sent = record_posts(SHELVES, devices)
    assert asyncio.run(p.set_shelf(1))["ok"] is True
    assert sent["path"] == "/api/me/devices/0/shelf"


def test_an_unreadable_choice_does_not_become_the_shared_library(record_posts):
    """None is a real choice here, so coercion failure must not collapse into
    it: that would silently empty the device's library instead of erroring."""
    p, sent = record_posts(SHELVES, DEVICES)
    result = asyncio.run(p.set_shelf("not-an-id"))
    assert result["ok"] is False
    assert sent == {}
