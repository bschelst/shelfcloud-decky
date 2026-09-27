# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""The Play-button matcher, against real labels from the live providers.

The rule is "a word from the list appears in the button's first line". These
tests pin that down, together with the labels that must NOT be clicked, since
the wrong control navigates away from the game.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import importlib.util

SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin", "shelfcloud-launch.py",
)
_spec = importlib.util.spec_from_file_location("launcher", SRC)
launcher = importlib.util.module_from_spec(_spec)
sys.argv = ["x"]
_spec.loader.exec_module(launcher)

WORDS = launcher.PLAY_WORDS
DENY = launcher.DENY_WORDS
PHRASES = launcher.DENY_PHRASES
CJK = re.compile(r"[　-鿿가-힯]")


def flatten(line):
    """Hyphens folded to spaces, so one phrase covers both spellings."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", line)).strip()


def words_of(line):
    """Split between words, never inside one — mirrors wordsOf in the JS. A
    naive split turned "Free-to-Play" into three words and clicked the wrong
    chip."""
    out = []
    for raw in re.split(r"[\s,;:|/·•—–]+", line):
        token = re.sub(r"^[^\w]+|[^\w]+$", "", raw, flags=re.UNICODE)
        if token:
            out.append(token)
    return out


def word_in(line, limit):
    """Mirrors hits() in the JS: deny and match both read the same slice."""
    if any(p in flatten(line) for p in PHRASES):
        return False
    tokens = words_of(line)[:limit]
    if any(t in DENY for t in tokens):
        return False
    return any(line[:12].find(w) >= 0 if CJK.search(w) else w in tokens for w in WORDS)


def hits(text, limit=4) -> bool:
    """Python mirror of one round of the matcher in CLICK_PLAY_JS."""
    line = (text or "").split("\n")[0].strip().lower()
    return bool(line) and word_in(line, limit)


def round_of(text, tag="button"):
    """Which round would take this label, if any. Round 1 is the tight rule (a
    short label that is itself an instruction); round 2 is the fallback for a
    label with a sub-label after it. Round 1 anywhere beats round 2."""
    line = (text or "").split("\n")[0].strip().lower()
    if not line:
        return None
    button = tag == "button"
    if len(line) <= 18 and word_in(line, 2 if button else 1):
        return 1
    if word_in(line, 4 if button else 1):
        return 2
    return None


# ------------------------------------------------------- the reported failure

def test_the_xbox_button_that_did_not_get_clicked():
    assert hits("NU SPELEN\nInbegrepen in je abonnement")


def test_the_old_rule_would_have_missed_it():
    """The counter-test: without it, the fix above proves nothing."""
    text = "NU SPELEN\nInbegrepen in je abonnement".strip().lower()
    assert len(text) > 14                 # rejected by the old length cap
    assert text not in WORDS              # and by the old exact match


# ------------------------------------------------------- labels that must fire

def test_localised_play_labels():
    for label in ("PLAY", "Play now", "NU SPELEN", "Nu spelen", "JETZT SPIELEN",
                  "Jouer maintenant", "JUGAR AHORA", "GIOCA ORA", "JOGAR AGORA",
                  "SPELA NU", "Spill nå", "SPIL NU", "PELAA", "ZAGRAJ TERAZ",
                  "Hrát", "JOACĂ ACUM", "OYNA", "ИГРАТЬ", "Грати"):
        assert hits(label), label


def test_two_line_labels_in_several_languages():
    for label in ("PLAY\nIncluded with Game Pass",
                  "NU SPELEN\nInbegrepen in je abonnement",
                  "JETZT SPIELEN\nIn deinem Abo enthalten",
                  "JOUER MAINTENANT\nInclus dans votre abonnement"):
        assert hits(label), label


def test_cjk_labels_match_as_substrings():
    """No word breaks in these scripts, so tokenising cannot work."""
    for label in ("プレイ", "遊ぶ", "开始游戏", "玩", "플레이", "게임하기"):
        assert hits(label), label


# --------------------------------------------------- labels that must NOT fire

def test_navigation_links_are_not_clicked():
    """Every one of these was on the live Xbox page beside the Play button"""
    for label in ("Overslaan naar hoofdinhoud", "Meer informatie over Cloud Gaming",
                  "PEGI 7", "XBOX-games", "XBOX Game Pass", "Microsoft-account",
                  "Bestellingen volgen", "Communautaire voorschriften",
                  "Xbox Live code of conduct", "Terms of transaction",
                  "Uw Californië privacykeuzes", "Feedback"):
        assert not hits(label), label


def test_words_embedded_in_longer_words_do_not_match():
    """'play' inside 'player' or 'playlist' is not a play button."""
    for label in ("Playlist", "Player profile", "Replay", "Display settings"):
        assert not hits(label), label


def test_the_boosteroid_subscribe_button_is_not_a_play_button():
    """A play word in a button that takes money — it opened the account page.
    Exactly 18 chars, so the length cap did not stop it either."""
    assert len("subscribe and play") == 18
    assert not hits("Subscribe and Play")
    assert round_of("Subscribe and Play") is None


def test_other_commerce_labels_containing_a_play_word():
    for label in ("Subscribe and play", "Buy and play", "Upgrade to play",
                  "Purchase to play", "Abonneren en spelen", "Premium play",
                  "Register to play"):
        assert round_of(label) is None, label


def test_the_deny_list_does_not_reject_the_xbox_button():
    """Xbox's sub-label says "Inbegrepen in je abonnement", and "abonnement" is
    a deny word — but it describes the game as included, not an offer. Deny must
    only judge the leading words, not the whole label."""
    assert "abonnement" in DENY
    assert hits("NU SPELEN\nInbegrepen in je abonnement")
    assert hits("NU SPELEN Inbegrepen in je abonnement")
    assert hits("PLAY\nIncluded with your subscription")


def test_the_geforce_now_filter_chip_is_not_a_play_button():
    """The launcher clicked this filter chip and landed on the games browser.
    Splitting made "Free-to-Play" three words ending in "play"; a hyphenated
    compound is one word."""
    assert words_of("free-to-play") == ["free-to-play"]
    assert not hits("Free-to-Play")
    assert round_of("Free-to-Play") is None


def test_the_chip_is_refused_however_it_is_spelled():
    """The tokeniser fix only covered the hyphenated form. GeForce NOW's
    aria-label spells it with spaces, and every word in "free to play" is
    innocent, so only the phrase catches it."""
    for label in ("Free to Play", "Free-to-Play", "FREE TO PLAY",
                  "free_to_play", "Free  to  Play"):
        assert not hits(label), label
        assert round_of(label) is None, label


def test_other_category_phrases_are_refused():
    for label in ("How to play", "Ways to play", "Cross play", "Local play",
                  "Play Store", "Google Play", "Play Pass", "Pay to play"):
        assert round_of(label) is None, label


def test_phrases_do_not_swallow_real_play_labels():
    """The denial is phrase-level, so a bare instructon is untouched."""
    for label in ("Play", "Play now", "NU SPELEN", "Jouer maintenant"):
        assert round_of(label) is not None, label


def test_other_hyphenated_labels_that_end_in_a_play_word():
    for label in ("Free-to-play", "Play-to-earn", "Cross-play", "Couch-play",
                  "Free-To-Play", "cloud-play"):
        assert not hits(label), label


def test_hyphenation_does_not_break_real_labels():
    """The split must still separate genuine words."""
    assert hits("Play now")
    assert hits("NU SPELEN")
    assert words_of("nu spelen") == ["nu", "spelen"]
    # Punctuation around a word is trimmed, so these read still as Play.
    for label in ("Play!", "(Play)", "» Play", "Play…"):
        assert hits(label), label


def test_a_sentence_mentioning_play_later_on_is_not_a_button():
    assert not hits("Discover thousands of games you can play today with Game Pass")


def test_an_inline_sub_label_still_matches():
    """Some providers render both parts in one text node, so innerText has no
    newline and the whole thing arrives as one long line."""
    assert hits("NU SPELEN Inbegrepen in je abonnement")
    assert hits("PLAY Included with Game Pass Ultimate")


def test_anchors_are_held_to_their_first_word():
    """Verified on the live page: this anchor sits in the Xbox footer."""
    assert not hits("Google Play Store", limit=1)
    assert hits("Play", limit=1)
    assert hits("Spelen", limit=1)


def test_empty_and_whitespace_labels():
    for label in ("", "   ", "\n\n", None):
        assert not hits(label)


def test_only_the_first_line_is_considered():
    """A play word buried in the sub-label must not make a link clickable."""
    assert not hits("Meer informatie\nSpelen met Game Pass")


# ------------------------------------------------------------- round ordering
#
# Widening the rule for Xbox's two-line button let stray long labels match on
# other providers, and the first match wins. So the tight rule sweeps the whole
# page before the loose one, and a real Play button is preferred over something
# that merely contains the word.

def test_a_bare_play_button_is_round_one():
    for label in ("Play", "PLAY", "NU SPELEN", "Jouer", "Spill nå", "ИГРАТЬ"):
        assert round_of(label) == 1, label


def test_a_label_with_a_sub_label_falls_to_round_two():
    assert round_of("NU SPELEN Inbegrepen in je abonnement") == 2
    assert round_of("PLAY Included with Game Pass Ultimate") == 2


def test_a_bare_play_button_beats_a_long_one_wherever_it_sits():
    """The regresion, stated directly: a plain Play button must win."""
    stray = "Play more games with a subscription"
    real = "Play"
    assert round_of(stray) == 2
    assert round_of(real) == 1
    assert round_of(real) < round_of(stray)


def test_a_long_label_without_a_leading_play_word_never_matches():
    assert round_of("Discover thousands of games you can play today") is None
    assert round_of("Meer informatie over Cloud Gaming") is None


def test_anchors_stay_tight_in_both_rounds():
    assert round_of("Google Play Store", tag="a") is None
    assert round_of("Play", tag="a") == 1
