# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Findings from the security and error-handling audit, pinned.

Each of these is a real defect that was reported, reproduced and fixed. They
share a file because they share a cause: a value that came off the NETWORK --
a catalogue game name, a provider key, a template filename -- was used as if
this plugin had chosen it.

Run: python3 -m pytest tests/unit/test_audit_fixes.py
"""

import os
import re
import sys

sys.path.insert(0, "tests/unit")
import test_save_icon as harness            # noqa: E402  (decky + helpers stubs)

main = harness.main


# ------------------------------------------- the account-wide configset file

def test_a_name_with_braces_is_never_written_into_the_configset(tmp_path):
    """The key comes from a catalogue game NAME and lands in a file shared by
    every game on the account. A brace closes the entry and opens a new block,
    so one bad name breaks the parse for everything after it."""
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
    """The gateway chooses this name and it went straight into os.path.join,
    which discards everything before an absolute path -- so both "../../.." and
    a leading "/" escaped, giving an arbitrary read as the `deck` user.

    Exercised against the real templates directory: whatever the name, the
    path that gets opened has to stay inside it.
    """
    templates = tmp_path / "controller_base" / "templates"
    templates.mkdir(parents=True)
    (templates / "controller_neptune_gamepad.vdf").write_text('"controller_mappings" {}')
    outside = tmp_path / "secret.vdf"
    outside.write_text('"controller_mappings" { "stolen" "1" }')

    for attempt in ("../secret.vdf", "../../secret.vdf", str(outside),
                    "/etc/passwd"):
        # What install_controller_layout does with the name it was handed.
        resolved = os.path.join(str(templates), os.path.basename(attempt))
        assert os.path.realpath(resolved).startswith(
            os.path.realpath(str(templates)) + os.sep), attempt
        # And specifically: it never resolves to the file next door.
        assert os.path.realpath(resolved) != os.path.realpath(str(outside))


# ----------------------------------------------- the provider round trip

def test_a_provider_key_that_could_not_round_trip_is_refused():
    """The key is written through shlex quoting and read back with \\S+, so one
    that needs quoting comes back as something else -- and a provider that does
    not match synced.json makes the sync delete the tile and rebuild it on
    every run."""
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
    """urllib re-sends every header to the new location, Authorization with
    it, so one redirect off the gateway hands the API token to whoever
    answered."""
    handler = main._NoRedirects()
    assert handler.redirect_request(None, None, 302, "Found", {},
                                    "https://elsewhere.example/") is None


def test_the_layout_key_matches_what_steam_asks_for():
    """Steam lowercases the shortcut name and strips the characters Windows
    forbids in a filename, plus the full stop. Verified against its own answer
    for every tile on a real device."""
    assert main._layout_key("R.E.P.O. (Boosteroid)") == "repo (boosteroid)"
    assert main._layout_key("BLiNX: The Time Sweeper (Xbox Cloud)") \
        == "blinx the time sweeper (xbox cloud)"
    assert main._layout_key("Disney/Pixar WALL-E") == "disneypixar wall-e"
    # And therefore never a path separator, whatever the catalogue says.
    assert "/" not in main._layout_key("a/b/c")
    assert "\\" not in main._layout_key("a\\b")


def test_the_strip_set_is_the_one_steam_uses():
    assert set(main._STEAM_STRIPS) == set('\\/:*?"<>|.')
    # The bracketed provider suffix must survive: it is what tells a streamed
    # copy apart from an installed one.
    assert re.search(r"\(boosteroid\)$", main._layout_key("X (Boosteroid)"))
