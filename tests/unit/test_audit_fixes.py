# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Fixes from the security audit. Each is a real defect: a value off the
network (game name, provider key, template filename) trusted as if it was ours.
"""

import os
import re
import sys

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main


# ------------------------------------------- the account-wide configset file

def test_a_name_with_braces_is_never_written_into_the_configset(tmp_path):
    """The key is a game name landing in an account-wide file. A brace in it
    would close the entry and break teh parse for everything after."""
    configset = tmp_path / "configset_controller_neptune.vdf"
    configset.write_text('"controller_config"\n{\n\t"existing"\n\t{\n\t\t"autosave" "1"\n\t}\n}\n')
    before = configset.read_text()

    main._register_configset(str(tmp_path), 'evil" } "pwned', "neptune")

    assert configset.read_text() == before, "markup reached the shared file"


def test_an_ordinary_name_is_still_registered(tmp_path):
    configset = tmp_path / "configset_controller_neptune.vdf"
    configset.write_text('"controller_config"\n{\n}\n')

    main._register_configset(str(tmp_path), "valheim (boosteroid)", "neptune")

    assert "valheim (boosteroid)" in configset.read_text()


def test_a_newline_cannot_forge_an_entry(tmp_path):
    configset = tmp_path / "configset_controller_neptune.vdf"
    configset.write_text('"controller_config"\n{\n}\n')
    before = configset.read_text()

    main._register_configset(str(tmp_path), "a\n\t\"b\"\n\t{\n\t}", "neptune")

    assert configset.read_text() == before


# --------------------------------------------- the template filename

def test_a_template_name_cannot_escape_the_templates_directory(tmp_path):
    """The gateway picks this name and it went into os.path.join, so "../.."
    and a leading "/" escaped. Whatever the name, the path must stay inside
    the templates dir.
    """
    templates = tmp_path / "controller_base" / "templates"
    templates.mkdir(parents=True)
    (templates / "controller_neptune_gamepad.vdf").write_text('"controller_mappings" {}')
    outside = tmp_path / "secret.vdf"
    outside.write_text('"controller_mappings" { "stolen" "1" }')

    for attempt in ("../secret.vdf", "../../secret.vdf", str(outside),
                    "/etc/passwd"):
        # What install_controller_layout does with the name it gets.
        resolved = os.path.join(str(templates), os.path.basename(attempt))
        assert os.path.realpath(resolved).startswith(
            os.path.realpath(str(templates)) + os.sep), attempt
        assert os.path.realpath(resolved) != os.path.realpath(str(outside))


# ----------------------------------------------- the provider round trip

def test_a_provider_key_that_could_not_round_trip_is_refused():
    """Written with shlex quoting and read back with \\S+, so a key needing
    quotes comes back wrong and the sync churns the tile every run."""
    assert main._PLAIN_KEY.match("amazon_luna")
    assert main._PLAIN_KEY.match("geforce_now")
    assert main._PLAIN_KEY.match("xbox-cloud.1")

    for bad in ("two words", "quote'key", 'double"key', "semi;colon",
                "", "-leading-dash", "new\nline"):
        assert not main._PLAIN_KEY.match(bad), bad


def test_the_reader_understands_a_quoted_provider():
    """An older tile may already carry one. It must be read, not churned."""
    sys.path.insert(0, "py_modules")
    import shortcuts

    for raw, expected in (
        (b"--provider amazon_luna", b"amazon_luna"),
        (b"--provider 'amazon luna'", b"amazon luna"),
        (b'--provider "amazon luna"', b"amazon luna"),
    ):
        match = shortcuts._PROVIDER.search(raw)
        assert match, raw
        value = next(g for g in match.groups() if g is not None)
        assert value == expected


# ------------------------------------------------------- the urllib fallback

def test_the_fallback_refuses_redirects():
    """urllib re-sends every header on a redirect, so one redirect off the
    gateway would hand the API token to whoever answered."""
    handler = main._NoRedirects()
    assert handler.redirect_request(None, None, 302, "Found", {},
                                    "https://elsewhere.example/") is None


def test_the_layout_key_matches_what_steam_asks_for():
    """Steam lowercases the name and strips the chars Windows forbids plus the
    dot. Checked against its own answer on a real device"""
    assert main._layout_key("R.E.P.O. (Boosteroid)") == "repo (boosteroid)"
    assert main._layout_key("BLiNX: The Time Sweeper (Xbox Cloud)") \
        == "blinx the time sweeper (xbox cloud)"
    assert main._layout_key("Disney/Pixar WALL-E") == "disneypixar wall-e"
    # so never a path separator, whatever the catalogue says
    assert "/" not in main._layout_key("a/b/c")
    assert "\\" not in main._layout_key("a\\b")


def test_the_strip_set_is_the_one_steam_uses():
    assert set(main._STEAM_STRIPS) == set('\\/:*?"<>|.')
    # the bracketed provider suffix must survive: it marks a streamed copy
    assert re.search(r"\(boosteroid\)$", main._layout_key("X (Boosteroid)"))
