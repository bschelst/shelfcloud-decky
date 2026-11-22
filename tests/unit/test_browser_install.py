# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Installing a browser from the panel.

Mostly about what must NOT happen: this feature downloads software and takes an
app id from the frontend straight into an argv.
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "py_modules"))

import browsers  # noqa: E402


# ------------------------------------------------------------- the allowlist

def test_only_the_two_offered_browsers_are_installable():
    assert browsers.is_installable("com.microsoft.Edge")
    assert browsers.is_installable("com.google.Chrome")


def test_everything_else_is_refused():
    """Including browsers Shelfcloud can USE but will not install unasked."""
    for app_id in ("com.brave.Browser", "com.vivaldi.Vivaldi",
                   "org.mozilla.firefox", "org.chromium.Chromium"):
        assert not browsers.is_installable(app_id), app_id


def test_hostile_values_are_refused():
    for app_id in ("", " ", None, 0, [], {}, "; rm -rf /", "com.microsoft.Edge; id",
                   "../../etc/passwd", "com.microsoft.Edge\nid",
                   "$(id)", "`id`", "com.microsoft.Edge --system"):
        assert not browsers.is_installable(app_id), repr(app_id)


# ------------------------------------------------------------ the command

def test_the_install_command_is_argv_not_a_shell_string():
    argv = browsers.install_command("com.microsoft.Edge")
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == browsers.FLATPAK_BIN
    assert argv[-1] == "com.microsoft.Edge"


def test_the_install_is_per_user_and_needs_no_root():
    """Backends run as `deck`. A --system install needs a polkit prompt Game
    Mode cannot show."""
    argv = browsers.install_command("com.google.Chrome")
    assert "--user" in argv
    assert "--system" not in argv


def test_the_install_cannot_stop_and_ask_a_question():
    """There is nobody at a terminal. Without these it would hang for ever."""
    argv = browsers.install_command("com.google.Chrome")
    assert "--assumeyes" in argv
    assert "--noninteractive" in argv


def test_it_installs_from_flathub():
    assert "flathub" in browsers.install_command("com.microsoft.Edge")


def test_the_remote_command_is_idempotent():
    """Runs before every install, so it must be a no-op when the remote is
    allready there."""
    argv = browsers.ensure_flathub_command()
    assert "--if-not-exists" in argv
    assert "--user" in argv
    assert browsers.FLATHUB_REPO in argv


def test_the_timeout_is_generous_but_finite():
    """A wedged download must end in a visible failure, not a forever spinner."""
    assert 5 * 60 <= browsers.INSTALL_TIMEOUT <= 30 * 60


# ------------------------------------------------------------- what is offered

def test_edge_is_offered_first_and_marked_recommended():
    assert browsers.INSTALLABLE[0]["app_id"] == "com.microsoft.Edge"
    assert browsers.INSTALLABLE[0]["recommended"] is True
    assert sum(1 for b in browsers.INSTALLABLE if b["recommended"]) == 1


def test_every_offer_has_what_the_panel_renders():
    for browser in browsers.INSTALLABLE:
        for key in ("app_id", "label", "note", "recommended"):
            assert key in browser, f"{browser.get('app_id')} is missing {key}"
        assert browser["note"], browser["app_id"]


def test_offers_are_a_subset_of_what_can_be_detected():
    """Installing something detect_browsers ignores would appear to do nothing."""
    known = {b["app_id"] for b in browsers.KNOWN_BROWSERS}
    for browser in browsers.INSTALLABLE:
        assert browser["app_id"] in known, browser["app_id"]


def test_an_already_installed_browser_is_not_offered(monkeypatch):
    monkeypatch.setattr(browsers, "detect_browsers",
                        lambda: [{"app_id": "com.microsoft.Edge", "label": "Microsoft Edge"}])
    offered = [b["app_id"] for b in browsers.installable_browsers()]
    assert "com.microsoft.Edge" not in offered
    assert "com.google.Chrome" in offered


def test_nothing_is_offered_when_both_are_present(monkeypatch):
    monkeypatch.setattr(browsers, "detect_browsers", lambda: [
        {"app_id": "com.microsoft.Edge"}, {"app_id": "com.google.Chrome"},
    ])
    assert browsers.installable_browsers() == []


def test_both_are_offered_on_a_bare_deck(monkeypatch):
    monkeypatch.setattr(browsers, "detect_browsers", lambda: [])
    assert len(browsers.installable_browsers()) == 2


def test_the_offer_list_is_a_copy(monkeypatch):
    """Mutating the returned list must not corrupt the module-level one."""
    monkeypatch.setattr(browsers, "detect_browsers", lambda: [])
    first = browsers.installable_browsers()
    first[0]["label"] = "tampered"
    assert browsers.installable_browsers()[0]["label"] == "Microsoft Edge"


# ------------------------------------------ a --user install is actually found

def test_a_user_install_is_detected(monkeypatch, tmp_path):
    """--user writes to ~/.local/share/flatpak, which must be scanned or the
    panel would say still no browser after a good install."""
    app_dir = tmp_path / "app" / "com.microsoft.Edge"
    app_dir.mkdir(parents=True)
    monkeypatch.setattr(browsers, "FLATPAK_ROOTS", [str(tmp_path / "app")])
    found = [b["app_id"] for b in browsers.detect_browsers()]
    assert "com.microsoft.Edge" in found


def test_only_edge_and_chrome_are_used(monkeypatch, tmp_path):
    """Brave, Vivaldi, Chromium installed next to them are not offered or picked."""
    for app_id in ("com.brave.Browser", "com.vivaldi.Vivaldi", "org.chromium.Chromium",
                   "com.google.Chrome", "com.microsoft.Edge"):
        (tmp_path / "app" / app_id).mkdir(parents=True)
    monkeypatch.setattr(browsers, "FLATPAK_ROOTS", [str(tmp_path / "app")])
    found = [b["app_id"] for b in browsers.detect_browsers()]
    assert found == ["com.microsoft.Edge", "com.google.Chrome"]


def test_a_chosen_brave_falls_back_on_a_supported_one(monkeypatch, tmp_path):
    for app_id in ("com.brave.Browser", "com.google.Chrome"):
        (tmp_path / "app" / app_id).mkdir(parents=True)
    monkeypatch.setattr(browsers, "FLATPAK_ROOTS", [str(tmp_path / "app")])
    assert browsers.resolve_browser("com.brave.Browser")["app_id"] == "com.google.Chrome"


def test_only_brave_installed_means_no_browser(monkeypatch, tmp_path):
    # the panel then offers to install Edge or Chrome
    (tmp_path / "app" / "com.brave.Browser").mkdir(parents=True)
    monkeypatch.setattr(browsers, "FLATPAK_ROOTS", [str(tmp_path / "app")])
    assert browsers.resolve_browser("com.brave.Browser") is None


def test_the_user_flatpak_root_is_scanned():
    assert any(".local/share/flatpak" in root for root in browsers.FLATPAK_ROOTS)
