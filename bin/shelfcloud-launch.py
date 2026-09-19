#!/usr/bin/env python3
# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Launch one cloud game and press the provider's Play button.

This is what a Shelfcloud Steam shortcut actually runs. It exists because the
provider's streaming URL contains a session id minted server-side when Play is
pressed, so the game page cannot be linked to directly — something has to click.

Steam launches this, not the plugin, so the automation has to live here rather
than in the plugin backend.

Usage:
    shelfcloud-launch.py <browser_app_id> <profile_dir> [url]
                         [--game-id N] [--provider KEY] [--no-autoplay] [--no-kiosk]

A game tile passes NO url. --game-id is what resolves a launch: the gateway is
asked where the game goes right now, so a changed URL or provider preference
takes effect without re-syncing, and the launch can be counted. When the
gateway does not answer, the offline URL is read from synced.json by game id.

It lives there rather than on this command line because Steam shows launch
options verbatim in a game's properties, and a provider deep link is not
something anyone needs to read there.

[url] is still accepted, for the website tile (which has no game id) and for
shortcuts written before the URL moved off the command line.

Exits when the browser exits, so Steam shows the game as running for the whole
session and the tile's playtime is accurate.
"""

import ipaddress
import json
import os
import ssl
import subprocess
import threading
import sys
import urllib.parse
import urllib.request
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py_modules"))

import browsers  # noqa: E402
import weburl  # noqa: E402
import cdp  # noqa: E402

CDP_PORT = 9223

# Identifies this launcher to the gateway. It must be SOMETHING: the proxy in
# front of the gateway rejects urllib's default with 403.
USER_AGENT = "Shelfcloud-Launcher/0.1 (+https://github.com/bschelst/shelfcloud)"
PAGE_TIMEOUT = 45.0
CLICK_TIMEOUT = 60.0

# Where this plugin's gateway lives. Fixed, and https.
#
# It used to be read from settings.json, which holds whatever address the
# plugin was last pointed at — on this developer's Deck, a plaintext
# http://192.168.x.x:8000. Every launch then sent the bearer token across the
# LAN in clear and accepted a launch URL from whoever answered that port. The
# plugin backend already ignores that setting and uses its own constant; the
# launcher disagreeing with it is what kept the hole open.
#
# Must match DEFAULT_GATEWAY in main.py — tests/unit/test_gateway_default.py
# checks that they agree.
GATEWAY = "https://shelfcloud.cc"

# Where the gateway used to live. Still serving, and still allowed as a
# launch target during the changeover; see ALLOWED_LAUNCH_DOMAINS.
LEGACY_HOST = "shelfcloud.schelstraete.org"

# The only places a game is allowed to launch.
#
# Checked HERE, on the device, not merely on the gateway. The gateway is a
# server, servers get compromised, and a launch URL it returns is opened in a
# kiosk-mode browser carrying live Microsoft, NVIDIA and Boosteroid sessions —
# with no address bar and no tab strip to show where it went. A phishing page
# in that window is indistinguishable from the real sign-in.
#
# Every launch URL in the production catalog is https on one of these hosts, so
# this costs nothing in practice.
#
# Luna is pinned to the FULL host, not to "amazon.com". The entry below is a
# suffix rule, so "amazon.com" would admit every host Amazon serves — retail
# pages, seller-controlled content, arbitrary S3-backed subdomains — to a
# fullscreen browser holding the user's provider sessions. "luna.amazon.com" is
# the only Amazon host a Shelfcloud launch ever needs.
#
# That host is also why the catalog stores luna.amazon.com links rather than the
# play.amazon.<tld> URL Luna's own API returns: one host to allow instead of a
# dozen country variants, and it redirects each user to their own Luna domain.
ALLOWED_LAUNCH_DOMAINS = (
    "geforcenow.com",     # play.geforcenow.com
    "boosteroid.com",     # cloud.boosteroid.com
    "xbox.com",           # www.xbox.com
    "luna.amazon.com",    # Amazon Luna — full host on purpose, see above
    # Our own site. The plugin makes a "Shelfcloud (website)" tile so the
    # catalogue can be browsed under Steam Input — Steam's built-in browser
    # forwards no controller input, so the d-pad is dead there.
    #
    # It was missing, so that tile could never launch: every URL goes through
    # this check, the gateway's own host was not on the list, and the launcher
    # refused its own website with exit 3. Nothing said so on screen — the tile
    # simply did nothing.
    #
    # DERIVED from GATEWAY rather than typed again, so the two cannot drift.
    # Admitting it grants nothing new: this is the host the launcher already
    # asks where every game goes, so trusting it to answer and refusing to open
    # it was the inconsistent half.
    urllib.parse.urlsplit(GATEWAY).hostname or "",
    # The previous home, kept while devices catch up.
    #
    # A tile's address is resolved by the gateway at launch, so the moment the
    # published Shelfcloud page moves to the new domain, a Deck still running
    # an older plugin is handed an address its allowlist does not know — and a
    # Deck on the new plugin would refuse the old one. Allowing both means the
    # tile works whichever side of the update a device is on. Drop it once
    # every device has been updated.
    LEGACY_HOST,
)


def _ssl_context():
    """Verified TLS, using certifi's CA bundle if available.

    The launcher runs as a standalone process (not inside Decky's sandbox),
    so it cannot import Decky's helpers module. But certifi is installed by
    Decky and reachable on the system path, so we use it directly.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def is_allowed_launch_url(url) -> bool:
    """Is this somewhere a cloud game may legitimately be played?

    https only: a launch carries provider session cookies, and http would both
    expose them and let anything on the path rewrite the page.

    The host must BE one of the allowed domains or sit beneath it. The dot in
    `"." + domain` is the whole check — without it "xbox.com.evil.com" ends
    with "xbox.com" and passes.

    Credentials in the authority are refused outright:
    "https://play.geforcenow.com@evil.com/" names the host evil.com while
    reading, at a glance, as though it does not.

    A URL containing a backslash, a tab or a newline is refused before it is
    parsed at all, because on those this checker and the browser DISAGREE about
    what the host is — and the browser is the one that opens the page.

    Python's urlsplit follows RFC 3986, where "\\" is an ordinary character.
    Chromium follows the WHATWG URL Standard, where "\\" ends the authority
    exactly like "/". So

        https://evil.com\\cloud.boosteroid.com/steal

    has host "evil.com\\cloud.boosteroid.com" here — which ends with
    ".boosteroid.com" and passes every check below — and host "evil.com" in the
    browser, which then opens an attacker's page in kiosk mode with the user's
    provider sessions. Tab, CR and LF are the same class of bug: a browser
    strips them from a URL before resolving it, so they can move the host
    boundary after this function has approved it.

    No legitimate provider URL contains any of them, so refusing is free.
    """
    if not url or not isinstance(url, str):
        return False
    # Before parsing, and on the RAW string: .strip() would remove a trailing
    # newline that is part of the attack.
    if any(c in url for c in "\\\t\r\n"):
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    if parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in ALLOWED_LAUNCH_DOMAINS)

# The provider's Play control.
#
# Text is the primary signal because the markup is minified and class names
# change without warning. It is not sufficient on its own: xbox.com serves a
# localised page (a Belgian browser gets "SPELEN", not "PLAY"), so the word list
# has to cover the languages a Deck is likely set to, and each provider adds a
# structural selector as a fallback that does not depend on language at all.
CLICK_PLAY_JS = """
(() => {
  const words = %(words)s;
  const selectors = %(selectors)s;

  const visible = (el) => {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };

  // A play control's label is its FIRST LINE. Providers hang a second line off
  // the same button — Xbox renders
  //     "NU SPELEN\\nInbegrepen in je abonnement"
  // (play now / included in your subscription) — so matching the whole text
  // fails, and a length cap on the whole text fails again. Both did.
  const firstLine = (el) =>
    (el.innerText || '').split('\\n')[0].trim().toLowerCase();

  // Match a WORD inside that line, not the whole line. "nu spelen",
  // "play now", "jetzt spielen" and "jouer maintenant" all carry a word from
  // the list; none of them equals one.
  //
  // `limit` is how many leading words to consider. A play control puts the
  // verb first, so looking only at the opening words is what separates
  // "NU SPELEN Inbegrepen in je abonnement" from a sentence that merely
  // mentions playing. It also covers providers that render the sub-label
  // inline, where innerText collapses to a single long line and a length cap
  // would throw the button away.
  const CJK = /[\\u3000-\\u9fff\\uac00-\\ud7af]/;

  // Split on separators BETWEEN words, never inside them. Splitting on every
  // non-alphanumeric character turned GeForce NOW's "Free-to-Play" filter chip
  // into ["free","to","play"], so the launcher clicked the filter and landed
  // on the games browser instead of the game. A hyphenated compound is one
  // word; only whitespace and list punctuation separate words.
  const wordsOf = (line) => line
    .split(/[\\s,;:|/·•—–]+/u)
    .map((t) => t.replace(/^[^\\p{L}\\p{N}]+|[^\\p{L}\\p{N}]+$/gu, ''))
    .filter(Boolean);

  // Labels that mean "spend money" or "manage your account", whatever else
  // they say. Boosteroid offers "Subscribe and Play", which contains a play
  // word and is not a play button: clicking it opened the account page.
  // A control carrying one of these is skipped outright — no play word can
  // redeem it.
  const deny = %(deny)s;

  // Phrases that are a category, not an instruction. "Free to Play" is a genre
  // filter; its words are individually innocent, so only the phrase catches
  // it. Hyphens are folded to spaces first, so "Free-to-Play" and
  // "Free to Play" are the same thing — GeForce NOW writes it both ways, in
  // the visible label and in the aria-label.
  const denyPhrases = %(deny_phrases)s;
  const flatten = (line) => line.replace(/[-_]+/g, ' ').replace(/\\s+/g, ' ').trim();

  const hits = (line, limit) => {
    if (!line) return false;
    if (denyPhrases.some((p) => flatten(line).includes(p))) return false;
    // Judge only the words being read, not the whole label. Xbox's button
    // says "NU SPELEN Inbegrepen in je abonnement" — "abonnement" is the
    // sub-label explaining the game is included, not an offer to subscribe,
    // and denying on it would break the very button this all started with.
    // Boosteroid's "Subscribe and Play" is refused because "subscribe" is one
    // of the leading words, which is where intent actually lives.
    const tokens = wordsOf(line).slice(0, limit);
    if (tokens.some((t) => deny.includes(t))) return false;
    return words.some((w) => CJK.test(w)
      ? line.slice(0, 12).includes(w)
      : tokens.includes(w));
  };

  // A provider's own selector is a hint, not a warrant.
  //
  // GeForce NOW's is button[aria-label*='Play' i], and its "Free to Play"
  // filter chip carries that in its aria-label too. querySelector returns the
  // FIRST match in the document, which was the chip — so the launcher opened
  // the games browser, and because the selector round ran before any text
  // rule, neither the deny list nor the tokeniser ever got a look.
  //
  // Every match is now read the same way a text match is: take its accessible
  // name, hold it to the same words, and skip it if it fails. The selector
  // narrows where to look; the label still has to say "play".
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (!visible(el)) continue;
      const label = ((el.getAttribute('aria-label') || '') || firstLine(el))
        .split('\\n')[0].trim().toLowerCase();
      if (!hits(label, 4)) continue;
      const r = el.getBoundingClientRect();
      return JSON.stringify({clicked: selector + ' [' + label.slice(0, 30) + ']', x: r.x + r.width/2, y: r.y + r.height/2});
    }
  }

  // Precision before reach, in two rounds.
  //
  // Round one takes only a control whose label IS a play instruction: the
  // first line is short and one of its words is a play word. "Play",
  // "NU SPELEN", "Jouer maintenant" all qualify.
  //
  // Round two is the fallback for a provider that hangs a sub-label off the
  // same control, giving one long line like
  //     "NU SPELEN Inbegrepen in je abonnement"
  //
  // The rounds exist because reach alone caused a regression: widening the
  // rule so Xbox's two-line button matched also let a stray long-labelled
  // button on other providers match FIRST, and the first match wins. Running
  // the tight rule across the whole page before the loose one means a real
  // "Play" button is always preferred over something that merely contains the
  // word, wherever each sits in the document.
  //
  // Within a round, buttons before anchors. The play control is a <button> on
  // every provider checked, while a game page is full of anchors — footer
  // navigation, legal links, a store link. Anchors are also held to their
  // first word: verified on the live Xbox page, "Google Play Store" is an
  // anchor whose SECOND word is "play".
  const ROUNDS = [
    // [maxLineLength, buttonWords, anchorWords]
    //
    // Two words, not three, in the tight round. A play instruction puts the
    // verb first or second — "Play", "Play now", "NU SPELEN", "JETZT
    // SPIELEN". At three, Boosteroid's "Subscribe and Play" qualified, and it
    // is exactly 18 characters so the length cap did not save it either.
    [18, 2, 1],      // a label that is itself an instruction
    [Infinity, 4, 1] // ... or one carrying a sub-label after it
  ];

  for (const [maxLen, buttonWords, anchorWords] of ROUNDS) {
    for (const [group, limit] of [['button, [role=button]', buttonWords], ['a', anchorWords]]) {
      for (const el of document.querySelectorAll(group)) {
        if (!visible(el)) continue;
        const line = firstLine(el);
        if (line.length > maxLen) continue;
        if (!hits(line, limit)) continue;
        const r = el.getBoundingClientRect();
        return JSON.stringify({clicked: line.slice(0, 40), x: r.x + r.width/2, y: r.y + r.height/2});
      }
    }
  }
  return 'not-found';
})()
"""

# Words meaning "play" in the languages SteamOS is commonly set to. Verified
# needed: xbox.com localises by IP, so an English-only list silently fails —
# a Belgian browser is served "NU SPELEN", not "PLAY".
#
# Single words only. The matcher looks for one of these as a word inside the
# button's first line, so multi-word labels are covered without listing every
# phrasing: "nu spelen", "jetzt spielen" and "jouer maintenant" are each caught
# by their verb alone.
#
# Shared by every provider — Boosteroid, GeForce NOW and Xbox all use this one
# list, so a language added here works everywhere.
PLAY_WORDS = [
    # English, Dutch, French, German, Spanish, Italian, Portuguese
    "play", "spelen", "speel", "jouer", "spielen", "jugar", "juega",
    "gioca", "giocare", "jogar", "jogue",
    # Nordic. Both the infinitive and the imperative: Xbox labels the button
    # "Spill nå" in Norwegian, not "Spille".
    "spela", "spille", "spill", "spil", "pelaa",
    # Central and Eastern Europe
    "graj", "zagraj", "hrát", "hrat", "hrať", "joacă", "joaca", "játék",
    "играть", "играй", "грати", "igraj",
    # Turkish, Greek
    "oyna", "παίξε", "παιξε",
    # CJK — matched as substrings, since these scripts have no word breaks
    "遊ぶ", "プレイ", "开始游戏", "立即游戏", "玩", "플레이", "게임하기",
]

# Words that disqualify a control no matter what else it says.
#
# Boosteroid offers "Subscribe and Play" — a play word in a button that takes
# money, and clicking it opened the account page instead of the game. A play
# word is not enough on its own; the label has to be an instruction to play,
# not to buy or to sign in.
#
# Any of these appearing anywhere in the label rejects it outright, so a
# provider adding "Upgrade and play" or "Buy to play" is refused too. The cost
# of being wrong here is asymmetric: failing to click means the user presses
# Play themselves, while clicking the wrong thing can start a purchase.
DENY_WORDS = [
    # subscribing
    "subscribe", "subscription", "subscriptions", "abonneren", "abonnement",
    "abonnieren", "abonner", "abonnement", "suscribirse", "suscripción",
    "abbonati", "abbonamento", "assinar", "assinatura", "prenumerera",
    "tilaa", "subskrybuj", "подписка", "подписаться", "abone", "abonelik",
    # buying
    "buy", "purchase", "kopen", "koop", "acheter", "comprar", "comprare",
    "kaufen", "köp", "kjøp", "køb", "osta", "kup", "купить", "satın",
    "checkout", "cart", "winkelwagen",
    # upgrading and pricing
    "upgrade", "premium", "trial", "plans", "pricing", "prijzen", "tarifs",
    "preise", "precios",
    # accounts
    "account", "profile", "profiel", "signin", "signup", "register",
    "registreren", "inloggen", "aanmelden", "connexion", "anmelden",
]

# Phrases that name a category rather than give an instruction.
#
# "Free to Play" is a genre filter on GeForce NOW. Every word in it is
# innocent, so only the phrase catches it — and it appears both hyphenated on
# the chip and spaced in the aria-label, which is how it slipped past a
# tokeniser fix: the provider's own selector matched the chip's aria-label
# before any text rule ran.
#
# Compared against the label with hyphens folded to spaces, so one entry
# covers both spellings.
DENY_PHRASES = [
    "free to play", "play to earn", "pay to play", "cross play",
    "couch play", "local play", "online play", "co op play",
    "play store", "google play", "play pass", "play together",
    "how to play", "ways to play", "learn to play", "ready to play",
    "gratis spelen", "samen spelen", "hoe te spelen",
    # Luna's top navigation, which sits on every game page. As a <div
    # role=button> it lands in the BUTTON group, where two leading words are
    # read — so "Remote Play" matched on "play" and would have navigated away
    # from the game. Luna keeps this label in English even on localised sites,
    # so one phrase covers every market.
    "remote play",
    # Signed out, Luna's PRIMARY button reads "Play free with Prime" and leads
    # to a subscription signup. The signed-out check and the :not(_log_in)
    # selector both already stop it; this is the third layer, because the cost
    # of being wrong here is starting a purchase rather than a game.
    "free with prime", "gratis met prime",
]

# Is the game actually running yet?
#
# This used to be "is there a <video> on the page", and that is not the same
# question. Luna's game detail page carries a TRAILER — paused, muted, 0x0 —
# so the answer was yes before anything had been clicked, the launcher logged
# "already streaming" and returned, and Play was never pressed. Verified on the
# device: one <video> with src m.media-amazon.com/images/I/*.mp4, paused=true,
# videoWidth=0, sitting right next to an enabled "Play now" button.
#
# A stream that is really running is PLAYING and has real dimensions. A trailer
# that happens to be auto-playing muted is still excluded by the size test on
# Luna, where it reports 0x0 until someone opens it.
IS_STREAMING_JS = """
(() => {
  if (location.href.includes('streaming')) return true;
  return [...document.querySelectorAll('video')].some(
    (v) => !v.paused && !v.ended && v.videoWidth > 1 && v.videoHeight > 1);
})()
"""

# What the page offered, for the log when nothing matched. Every provider
# words and marks up its Play button differently and changes it without
# notice, so a bare "not found" costs a trip to the device to find out why.
LIST_CANDIDATES_JS = """
(() => {
  const out = [];
  for (const el of document.querySelectorAll('button, a, [role=button]')) {
    const box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) continue;
    const line = (el.innerText || '').split('\\n')[0].trim();
    if (!line || line.length > 40) continue;
    out.push(el.tagName.toLowerCase() + ':' + line);
  }
  return out;
})()
"""

# What a provider looks like when the browser profile is not signed in.
#
# Both checks were verified against the live sites while signed out. They are
# structural rather than textual on purpose: the visible wording is localised,
# but the login URL and the routing are not.
PROVIDERS = {
    "boosteroid": {
        "label": "Boosteroid",
        "signed_out": "!!document.querySelector('a[href*=\"/login\"], input[type=password]')",
        "selectors": [],
        "setup_url": "https://cloud.boosteroid.com/",
    },
    "xbox_cloud": {
        "label": "Xbox Cloud",
        # Signed out, the page offers /auth/msa?action=logIn and no Play
        # control. The locale prefix varies (/nl-BE/, /en-US/); the rest does
        # not.
        "signed_out": "!!document.querySelector('a[href*=\"/auth/msa\"]')",
        # Signed in, the Play control links into the launch route.
        "selectors": ["a[href*='/play/launch/']"],
        "setup_url": "https://www.xbox.com/play",
    },
    "geforce_now": {
        # The deeplink bounces to #/loginwall when the profile is not signed
        # in — an unambiguous signal that needs no DOM inspection at all.
        "label": "GeForce NOW",
        "signed_out": "location.hash.includes('loginwall')",
        "selectors": ["button[aria-label*='Play' i]"],
        "setup_url": "https://play.geforcenow.com/",
    },
    "amazon_luna": {
        "label": "Amazon Luna",
        # Luna refuses to play on Linux.
        #
        # Not a capability problem — measured on the device, this browser
        # reports "probably" for h264, h265, vp9 AND av1, has MediaSource and
        # has EME. Luna is reading the platform out of the User-Agent and
        # refusing on that alone, so the page shows "Unsupported Browser" and
        # the stream never starts even though everything it needs is present.
        #
        # Verified over CDP on the live page: overriding only
        # navigator.userAgent is enough — Luna does not consult
        # navigator.userAgentData, which is why a command-line flag works here
        # and no CDP-session override is needed (that one dies with the
        # debugging session anyway).
        #
        # Same Edge version the browser really is, so nothing else about the
        # page changes; only the platform is different.
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
        ),
        # Reporting Windows has a side effect: Luna then believes a desktop app
        # might be installed and pokes "luna://" to find out, which Chromium
        # surfaces as "This site is trying to open xdg-open". See
        # NEUTRALISE_APP_HANDOFF_JS.
        "startup_js": "NEUTRALISE_APP_HANDOFF_JS",
        # The launch URL is luna.amazon.com, and Luna REDIRECTS it to the
        # viewer's own country: luna.amazon.de, luna.amazon.co.uk,
        # luna.amazon.com.be. Matching on the host we asked for therefore finds
        # the page in some markets and not others.
        #
        # It is worth being precise about how this hid: Belgium redirects to
        # luna.amazon.com.be, which CONTAINS "luna.amazon.com", so the substring
        # match succeeded here by accident. Germany (luna.amazon.de) and the UK
        # (luna.amazon.co.uk) do not contain it and would have found nothing,
        # waited out the 45s timeout and left Play to the user — a bug that
        # only ever appeared for people abroad.
        "page_match": "luna.amazon.",
        # Two element ids, both read off the live signed-out page. Ids, not
        # text: Luna localises by IP like Xbox does, and a Belgian browser is
        # served "Inloggen" rather than "Sign in".
        #
        # This check earns its keep more than the others. Signed out, Luna's
        # PRIMARY button reads "Speel gratis met Prime" — it carries a play
        # word, is not on any deny list, and leads to a Prime signup. Detecting
        # signed-out first is what stops the launcher pressing it.
        "signed_out": (
            "!!document.querySelector('#item_secondary_nav_button_sign_in, "
            "[data-test-id=\"primary_entitled_button_log_in\"]')"
        ),
        # Read off the live signed-in page rather than guessed. Both states use
        # the same prefix and differ only in the suffix:
        #
        #   primary_entitled_button_purchase_detail   signed in  -> "Play now"
        #   primary_entitled_button_log_in            signed out -> "Play free with Prime"
        #
        # The :not() is the important half. Signed out, that button carries a
        # play word, is on no deny list, and leads to a Prime signup — so a
        # selector matching the prefix alone would press "subscribe" for anyone
        # whose session had expired. Excluding the log-in suffix means this can
        # only ever match a control that plays something.
        "selectors": [
            'button[data-test-id^="primary_entitled_button"]'
            ':not([data-test-id$="_log_in"])'
        ],
        "setup_url": "https://luna.amazon.com/",
    },
}


def provider_profile(key: str) -> dict:
    return PROVIDERS.get(key or "", {
        "label": key or "the provider",
        "signed_out": "false",
        "selectors": [],
        "setup_url": "",
    })



# Where the plugin keeps its gateway address and credential. The launcher is
# started by Steam, not by the plugin, so it has to read them itself.
SETTINGS_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/settings.json")

# What the plugin remembers about every tile it made, keyed by game id. The
# offline launch URL lives here rather than in the Steam shortcut, because
# Steam displays launch options verbatim in a game's properties and a provider
# deep link is not something anyone needs to read there.
STATE_FILE = os.path.expanduser(
    "~/homebrew/settings/Shelfcloud/synced.json")
RESOLVE_TIMEOUT = 8


def stored_fallback(game_id: str, provider: str = "") -> str:
    """The offline launch URL for this game, or "" if there is not a usable one.

    Never raises: this runs on the path where the gateway has ALREADY failed,
    so an unreadable or malformed state file must degrade to "no fallback"
    rather than take the launch down with it.

    The URL is checked here as well as at the end of main(). This file is
    written by the plugin, but it is also just a file on disk — and it is read
    precisely when the gateway cannot be consulted to correct it.
    """
    if not game_id:
        return ""
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            state = json.load(handle)
        entry = state.get(str(game_id))
        url = (entry or {}).get("url")
    except Exception:
        return ""
    return url if isinstance(url, str) and url_is_acceptable(url, provider) else ""

# Steam runs this script and captures none of its output — stdout goes nowhere
# a person can read. That mattered: when a launch quietly used the stored URL
# instead of asking the gateway, there was no way to tell from the outside,
# because a fallback is indistinguishable from success as far as the user is
# concerned. The game still starts. Only the gateway's silence gives it away.
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOMEBREW_DIR = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
LOG_FILE = os.path.join(_HOMEBREW_DIR, "logs", "Shelfcloud", "shelfcloud-launch.log")

# Kept small on purpose. This lives on a Deck's internal storage and nothing
# rotates it, so it trims itself rather than growing without end.
LOG_MAX_BYTES = 256 * 1024
LOG_KEEP_BYTES = 128 * 1024


def log(message: str) -> None:
    """Write one timestamped line, to the log file and to stdout.

    Never raises: a launcher that dies because it could not log would be a
    worse bug than the one this exists to diagnose.
    """
    line = f"[shelfcloud] {message}"
    print(line)  # keep: log() is the writer
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            with open(LOG_FILE, "rb") as handle:
                handle.seek(-LOG_KEEP_BYTES, os.SEEK_END)
                handle.readline()          # drop the half line we landed in
                tail = handle.read()
            with open(LOG_FILE, "wb") as handle:
                handle.write(tail)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except Exception:
        pass


def resolve_launch(game_id: str, fallback_url: str, provider: str = "") -> tuple:
    """Ask the gateway where this game launches right now.

    The URL used to be frozen into the Steam shortcut at sync time, so a launch
    never reached the gateway: a URL that changed upstream stayed broken until
    the next sync, and a changed provider preference needed a re-sync. Asking at
    launch fixes both, and lets the gateway count what actually gets played.

    The frozen URL is kept as a fallback and that is not belt-and-braces: the
    gateway is a machine on the user's LAN, and a game must not become
    unplayable because it is asleep. A slow gateway must not delay a launch
    either, hence the short timeout.

    Every path out of here is logged with its reason. A fallback is invisible
    from the user's side — the game starts either way — so without a line
    saying which route was taken, "the launch was not recorded" and "the
    gateway was never asked" look identical from the server.
    """
    # The offline route, looked up before the gateway is tried so every path
    # below has something to fall back to. A shortcut written before the URL
    # moved off the command line still passes one positionally; that keeps
    # working, and is preferred only when local state has nothing.
    fallback_url = fallback_url or stored_fallback(game_id, provider)

    if not game_id:
        log("no --game-id on this shortcut; using the stored link "
            "(re-sync to get gateway resolution)")
        return fallback_url, "baked-in"

    try:
        with open(SETTINGS_FILE, encoding="utf-8") as handle:
            settings = json.load(handle)
    except Exception as exc:
        log(f"cannot read {SETTINGS_FILE} ({exc}); using the stored link")
        return fallback_url, "baked-in"

    # The address is the constant above, NOT settings["gateway_url"]. That key
    # still exists in settings written by older builds and points at whatever
    # the plugin was last aimed at — here, a plaintext LAN address. Reading it
    # put the bearer token on the wire in clear and took a launch URL from
    # whoever answered that port.
    gateway = GATEWAY
    token = settings.get("api_token")
    if not token:
        log("no credential in settings — not signed in; using the stored link. "
            "This launch will not be counted.")
        return fallback_url, "not signed in"

    try:
        request = urllib.request.Request(
            f"{gateway}/api/me/launch/{game_id}",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                # Without this, urllib sends "Python-urllib/3.x" and the
                # reverse proxy in front of the gateway answers 403 — measured:
                # every other User-Agent reaches the app and gets a normal 401
                # for a bad token, that one never arrives at all.
                #
                # The failure was invisible, which is why it lasted: the
                # launcher falls back to the stored URL and the game still
                # starts, so nothing looked wrong. It only meant no launch was
                # ever resolved live and none was ever counted. The plugin
                # backend has always sent a User-Agent; this one never did.
                "User-Agent": USER_AGENT,
            },
        )
        started = time.monotonic()
        # Verified TLS against the bundled CA bundle, the same one main.py
        # uses: Decky's embedded Python ships its own libssl and has been seen
        # missing roots that a Cloudflare-fronted host presents.
        with urllib.request.urlopen(request, timeout=RESOLVE_TIMEOUT,
                                    context=_ssl_context()) as response:
            payload = json.loads(response.read().decode())
        elapsed = int((time.monotonic() - started) * 1000)
    except Exception as exc:
        # Distinguish "the gateway said no" from "the gateway is not there":
        # a 401 needs a re-login, a refused connection needs the box woken up.
        status = getattr(exc, "code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        log(f"gateway {gateway} did not answer for game {game_id} "
            f"({detail}: {exc}); using the stored link")
        return fallback_url, "baked-in"

    url = payload.get("launch_url") if isinstance(payload, dict) else None
    if not url:
        log(f"gateway answered for game {game_id} with no launch_url "
            f"({str(payload)[:120]}); using the stored link")
        return fallback_url, "baked-in"

    # Do not trust the gateway with where this device browses. A correct
    # gateway only ever returns one of the permitted provider hosts; one that
    # returns anything else is either compromised or answering for someone who
    # is, and either way this is the point at which it stops.
    if not url_is_acceptable(url, provider):
        log(f"gateway returned a URL this tile may not open "
            f"({str(url)[:120]!r}) — REFUSED, using the stored link")
        return fallback_url, "refused gateway url"

    log(f"gateway resolved game {game_id} -> {payload.get('name') or '?'} "
        f"via {payload.get('provider') or '?'} in {elapsed} ms")
    return url, f"gateway ({payload.get('provider')})"

# Where a page tile may go, and which providers get that rule instead of the
# five-host provider allowlist above. Both live in py_modules/weburl.py, which
# main.py imports too — the decision to make a shortcut and the decision to
# open it have to agree, and they only do if there is one copy of the rule.
WEBSITE_PROVIDERS = weburl.WEBSITE_PROVIDERS
is_allowed_website_url = weburl.is_allowed_website_url


def build_website_command(app_id: str, profile: str, url: str) -> list:
    """Chromium command line for a page, which is not the one a game gets.

    Three deliberate differences, and each is the reason the allowlist can be
    relaxed for these tiles at all:

    * A SEPARATE PROFILE. The game profile holds live Microsoft, NVIDIA,
      Boosteroid and Luna sessions. A page somebody added has no business
      within reach of those, and a separate --user-data-dir is what keeps it
      out — cookies, storage and saved passwords all live in that directory.

    * NO REMOTE DEBUGGING PORT. A game window opens one so the launcher can
      press Play through CDP. That port is unauthenticated and accepts commands
      from anything that can reach it, including script in the page it is
      debugging; it is worth it for a fixed set of provider sites and is not
      worth it for an arbitrary one. Nothing here needs it, so it is absent
      rather than merely unused.

    * NO KIOSK. Kiosk hides the address bar, and the address bar is how a
      person can tell where they actually are. That is exactly the check the
      allowlist performs for game tiles, handed back to the user for pages the
      catalogue cannot vouch for.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        "--window-size=1280,800",
        "--start-maximized",
        "--class=shelfcloud-web",
        *browsers.BASE_FLAGS,
        url,
    ]


def website_profile(profile: str) -> str:
    """Where a page's cookies live: beside the game profile, never inside it."""
    return os.path.join(os.path.dirname(profile.rstrip("/")) or ".",
                        os.path.basename(profile.rstrip("/")) + "-web")

def url_is_acceptable(url, provider: str) -> bool:
    """Which of the two gates applies, decided by what kind of tile this is.

    One function so that the three places a URL is checked — the gateway's
    answer, the link stored on disk, and the last step before a browser opens —
    cannot end up disagreeing about which rule a page is held to.
    """
    if provider in WEBSITE_PROVIDERS:
        return is_allowed_website_url(url)
    return is_allowed_launch_url(url)


def build_browser_command(app_id: str, profile: str, url: str, kiosk: bool = True,
                          user_agent: str = "") -> list:
    """Chromium command line for one session.

    Kiosk is right for streaming and wrong for signing in: a Microsoft or
    NVIDIA login can bounce through pages that need a visible tab strip and a
    way back, and a kiosk window offers neither.
    """
    return [
        "/usr/bin/flatpak", "run",
        "--branch=stable", "--arch=x86_64",
        f"--filesystem={profile}",
        app_id,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={CDP_PORT}",
        "--window-size=1280,800",
        "--class=shelfcloud",
        *browsers.BASE_FLAGS,
        *([f"--user-agent={user_agent}"] if user_agent else []),
        *(["--kiosk"] if kiosk else []),
        url,
    ]


# Luna asks the desktop app to take over, and on a Deck there is no desktop app.
#
# Read off Luna's own bundle. AppLauncher builds "luna://play.amazon<tld>/..."
# and hands it to launchProtocol, which for Chrome ends in:
#
#     function openUriWithHiddenFrame(uri, fail, success) {
#         const timer = setTimeout(() => { fail(); ... }, 500);
#         let frame = document.querySelector("#hiddenIframe")
#                     || _createHiddenIframe(document.body, "about:blank");
#         ... window "blur" -> success ...
#         frame.contentWindow.location.href = uri;
#     }
#
# So it pokes the scheme at a hidden iframe and reads 500ms of silence as "app
# not installed". Chromium turns that poke into the "This site is trying to
# open xdg-open" prompt, which sits on top of the game until it is dismissed.
#
# There is NO browser flag for this — external-protocol handling is deliberately
# not switchable, and the enterprise policy that can block it (URLBlocklist)
# would mean writing a system-wide policy file as root on the user's device.
#
# So: supply #hiddenIframe ourselves, with a contentWindow whose location.href
# setter does nothing. Luna's querySelector finds ours, skips making its own,
# pokes the stub, hears nothing, and takes its own "not installed" branch —
# which calls .catch(() => this.provision()) and streams in the browser.
#
# That is not a trick to defeat a check. It is the honest answer: there is no
# Luna desktop app on this machine, and the only thing being suppressed is a
# prompt asking the user to confirm that.
#
# An own property shadows the prototype getter, which is why this works at all:
# Location.href is [Unforgeable], so the real one can never be overridden.
NEUTRALISE_APP_HANDOFF_JS = """
(() => {
  const install = () => {
    try {
      if (document.getElementById('hiddenIframe')) return true;
      const host = document.body || document.documentElement;
      if (!host) return false;          // document-start: nothing to attach to yet
      const frame = document.createElement('iframe');
      frame.id = 'hiddenIframe';
      frame.style.display = 'none';
      // An OWN property shadows the prototype getter. This is the only way in:
      // Location.href is [Unforgeable], so the real one can never be replaced.
      Object.defineProperty(frame, 'contentWindow', {
        configurable: true,
        get() { return { location: { set href(_) {}, get href() { return ''; } } }; },
      });
      host.appendChild(frame);
      return true;
    } catch (e) { return true; }       // never break the page over this
  };
  // This runs before the page's own scripts, so the DOM is usually still empty.
  // Luna only looks for #hiddenIframe when Play is pressed, so DOMContentLoaded
  // is early enough by a wide margin.
  if (!install()) {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  }
})();
"""

VIRTUAL_GAMEPAD_JS = """
// Chrome hides gamepads until a physical button press (W3C fingerprinting
// mitigation). Cloud providers check on load and show "use your phone".
//
// This overrides navigator.getGamepads to return a virtual Xbox 360 pad in
// slot 0 until the real API unlocks. Once Chrome exposes the real pads, the
// override passes through to them transparently.
(() => {
  const real = navigator.getGamepads.bind(navigator);
  const fakeBtn = () => ({pressed: false, touched: false, value: 0});
  const fakePad = {
    id: 'Xbox 360 Controller (XInput STANDARD GAMEPAD)',
    index: 0, connected: true, mapping: 'standard',
    axes: [0, 0, 0, 0],
    buttons: Array.from({length: 17}, fakeBtn),
    timestamp: performance.now(),
    hapticActuators: [], vibrationActuator: null,
  };

  navigator.getGamepads = function() {
    const pads = real();
    if (pads && [...pads].some(Boolean)) return pads;
    const out = [fakePad, null, null, null];
    return out;
  };

  // Fire gamepadconnected so listeners that wait for the event see it too.
  window.dispatchEvent(new GamepadEvent('gamepadconnected', {gamepad: fakePad}));
})();
"""


def wake_gamepads(url: str, provider: str = "") -> None:
    """No-op. The virtual gamepad override broke real gamepad detection on
    Boosteroid and GeForce NOW by masking the native getGamepads() result.
    Luna's injection lives in hold_platform_override and is unaffected."""
    pass



def log_browser_gamepads(url: str, provider: str = "") -> None:
    """Record what the PAGE sees, which is the only view that decides anything.

    The launcher can list Steam's virtual pads all day; the provider's web
    player reads navigator.getGamepads() and generally drives whatever sits at
    index 0. If index 0 is the Deck's own controls, an external controller does
    nothing until Steam's order is changed — and Steam does not persist that
    order for these tiles, so it is changed again every launch.

    Chrome exposes a gamepad to a page only after that pad sends input, so an
    empty list here is not a fault: it means nothing had been touched yet, and
    it is worth knowing too.

    Best effort and never fatal: this is a diagnostic, not a feature.
    """
    profile = provider_profile(provider)
    marker = profile.get("page_match") or (url.split("/")[2] if "//" in url else url)
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target or not target.get("webSocketDebuggerUrl"):
        return
    try:
        seen = cdp.evaluate(target["webSocketDebuggerUrl"], """
            JSON.stringify([...navigator.getGamepads()].map(
                (g, i) => g ? {i, id: g.id, axes: g.axes.length,
                               buttons: g.buttons.length} : {i, id: null}))
        """)
    except cdp.CDPError as exc:
        log(f"could not read the page's gamepads: {exc}")
        return
    log(f"browser gamepads: {seen}")


def hold_platform_override(url: str, provider: str = ""):
    """Make the page believe it is on a supported OS, for the whole session.

    Luna has TWO gates, and they read different things:

      "Unsupported Browser" — reads the User-Agent STRING. The --user-agent
                              flag in build_browser_command settles this one.
      "Unsupported OS"      — reads navigator.platform / navigator.userAgentData,
                              which that flag does not touch. Measured with the
                              flag on: platform "Linux x86_64", uaData "Linux".

    So the flag alone gets past the first gate and straight into the second,
    which is exactly what happened. This closes the second one.

    Returns the held socket, which the caller must keep alive — see
    cdp.hold_platform. Returns None when the provider asks for nothing, which
    is every provider but Luna.

    Best effort, like try_autoplay: if this fails the user is no worse off than
    before it existed, and a game that does not stream is not worth aborting a
    launch over.
    """
    profile = provider_profile(provider)
    user_agent = profile.get("user_agent")
    if not user_agent:
        return None

    marker = profile.get("page_match") or (url.split("/")[2] if "//" in url else url)
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target or not target.get("webSocketDebuggerUrl"):
        log(f"page never appeared for the platform override (looked for {marker!r})")
        return None

    held = cdp.hold_platform(target["webSocketDebuggerUrl"], user_agent,
                             profile.get("platform", "Windows"))
    wants_shim = profile.get("startup_js") == "NEUTRALISE_APP_HANDOFF_JS"
    if wants_shim:
        cdp.add_startup_script(held, NEUTRALISE_APP_HANDOFF_JS)
    cdp.add_startup_script(held, VIRTUAL_GAMEPAD_JS)
    cdp.reload_page(held)
    log("injected virtual gamepad for this session")
    # add_startup_script enables the Page domain, which pushes events into the
    # socket. Drain them or the socket wedges.
    if True:
        # add_startup_script had to enable the Page domain, so from here on the
        # browser is pushing events at this socket. Drain them or it wedges.
        cdp.drain_in_background(held)
    log(f"{profile['label']}: reporting {profile.get('platform', 'Windows')} "
        "for this session")
    return held


FULLSCREEN_CHECK_JS = """
(() => {
    var buttons = document.querySelectorAll("button, [role=button]");
    for (var i = 0; i < buttons.length; i++) {
        var text = (buttons[i].innerText || "").toLowerCase();
        if (text.indexOf("full screen") >= 0 || text.indexOf("fullscreen") >= 0) {
            var rect = buttons[i].getBoundingClientRect();
            if (rect.height > 0 && rect.width > 0) {
                return JSON.stringify({x: rect.x + rect.width/2, y: rect.y + rect.height/2, text: buttons[i].innerText.slice(0,40)});
            }
        }
    }
    return "none";
})()
"""


def _click_fullscreen_if_needed(ws_url, timeout=15):
    """Wait for a fullscreen prompt and click it. Best effort."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = cdp.evaluate(ws_url, FULLSCREEN_CHECK_JS, timeout=5)
            if r and r != "none":
                hit = json.loads(r)
                cdp.dispatch_click(ws_url, hit["x"], hit["y"])
                log(f"clicked:{hit['text']}")
                return
        except Exception:
            pass
        time.sleep(1.5)


def try_autoplay(url: str, provider: str = "") -> None:
    """Press Play once the game page is up. Best effort — never fatal.

    If anything here fails the user simply presses Play themselves, which is the
    behaviour they would have had anyway. Nothing is worth killing the session
    over.
    """
    profile = provider_profile(provider)
    click_js = CLICK_PLAY_JS % {
        "words": json.dumps(PLAY_WORDS),
        "deny": json.dumps(DENY_WORDS),
        "deny_phrases": json.dumps(DENY_PHRASES),
        "selectors": json.dumps(profile["selectors"]),
    }

    # How to recognise the game's page once the browser has it open.
    #
    # The host we asked for is the right default — Boosteroid, GeForce NOW and
    # Xbox all stay on it (Xbox localises by PATH, /nl-BE/play/..., so its host
    # never changes). A provider that redirects across country DOMAINS declares
    # its own marker instead; see amazon_luna above.
    host_marker = url.split("/")[2] if "//" in url else url
    marker = profile.get("page_match") or host_marker
    target = cdp.wait_for_page(CDP_PORT, marker, timeout=PAGE_TIMEOUT)
    if not target:
        log(f"game page never appeared (looked for {marker!r}); "
            "leaving Play to the user")
        return

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return

    deadline = time.monotonic() + CLICK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if cdp.evaluate(ws_url, IS_STREAMING_JS):
                log("already streaming")
                return

            # Stop early when the profile simply is not signed in. Clicking has
            # nothing to find, and spending the full timeout on it buries the
            # one message the user actually needs in a minute of silence.
            if cdp.evaluate(ws_url, profile["signed_out"]):
                log(f"not signed in to {profile['label']} in this browser.")
                log("Sign in on this page — the profile is kept, "
                      "so this is a one-off. Games launch straight in afterwards.")
                return

            result = cdp.evaluate(ws_url, click_js)
            if isinstance(result, str):
                try:
                    hit = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    hit = None
                if isinstance(hit, dict) and "clicked" in hit:
                    cdp.dispatch_click(ws_url, hit["x"], hit["y"])
                    log(f"clicked:{hit['clicked']}")
                    _click_fullscreen_if_needed(ws_url)
                    return
        except cdp.CDPError as exc:
            # Page may still be loading, or navigating between documents.
            log(f"waiting for Play ({exc})")
        time.sleep(1.5)

    # Nothing is auto-accepted on the user's behalf. Both providers show a
    # consent dialog on a fresh profile — Microsoft's cookie banner, NVIDIA's
    # "AGREE & CONTINUE" — and clicking an agreement for someone is not the
    # launcher's business, so it says so instead.
    log(f"Play not found on {profile['label']}. If a cookie or "
          "terms dialog is showing, accept it once and press Play.")

    # Then say what WAS on the page.
    #
    # Every provider labels this button differently and changes it without
    # warning, so "not found" on its own costs a round trip: connect to the
    # Deck, hope the page is still open, and read the DOM. Listing the
    # candidates here turns the next failure into a log line that names the
    # button we should have matched.
    try:
        labels = cdp.evaluate(ws_url, LIST_CANDIDATES_JS)
        if labels:
            log("clickable controls on the page were: "
                + " | ".join(str(x) for x in labels[:12]))
    except Exception:
        pass


def active_gamepads() -> list:
    """Virtual gamepads Steam Input has created, as /dev/input/event* paths.

    Steam creates one per connected controller when a game starts. The browser
    enumerates these to expose the Gamepad API.
    """
    pads = []
    try:
        with open("/proc/bus/input/devices", encoding="utf-8") as handle:
            blocks = handle.read().split("\n\n")
    except OSError:
        return pads

    for block in blocks:
        if "X-Box" not in block and "Xbox" not in block:
            continue
        # The NAME is the whole point of logging these. Steam renumbers its
        # virtual pads every session — the same controller was event19 one
        # night and event18 the next — so a path says nothing about WHICH
        # controller it is, while "pad 0" / "pad 1" is Steam's slot order, and
        # slot order is what decides who is player one.
        name = ""
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        pads.append(f"{name} [/dev/input/{handler}]" if name
                                    else f"/dev/input/{handler}")
    return pads


def wait_for_gamepad(timeout: float = 6.0, settle: float = 1.0) -> list:
    """Wait until Steam Input has FINISHED making its virtual pads.

    The browser enumerates input devices once, at startup, and udev hotplug
    events do not reach it reliably inside the flatpak sandbox — so a pad that
    appears after it starts may never be noticed at all.

    This used to return on the first pad it saw, which is the wrong moment when
    two controllers are connected: Steam creates them a fraction apart, so
    returning on pad 0 could launch the browser before pad 1 existed, and the
    external controller was then invisible for the whole session. The symptom
    is indistinguishable from the controller being in the wrong slot, which is
    part of why that was hard to pin down.

    So wait for the set to stop GROWING rather than to become non-empty: once
    the list has been unchanged for `settle` seconds, Steam is done. Costs
    about a second on a launch that already takes several.

    Returns whatever exists at `timeout` if pads keep churning, and [] if none
    ever appear — both are honest answers, and the caller only logs them.
    """
    deadline = time.monotonic() + timeout
    pads: list = []
    last_change = None

    while time.monotonic() < deadline:
        current = active_gamepads()
        if current != pads:
            pads = current
            last_change = time.monotonic()
        elif pads and last_change is not None \
                and time.monotonic() - last_change >= settle:
            return pads
        time.sleep(0.3)
    return pads

def main() -> int:
    argv = sys.argv[1:]
    autoplay = "--no-autoplay" not in argv
    kiosk = "--no-kiosk" not in argv

    provider = ""
    if "--provider" in argv:
        index = argv.index("--provider")
        if index + 1 < len(argv):
            provider = argv[index + 1]
            argv = argv[:index] + argv[index + 2:]

    game_id = ""
    if "--game-id" in argv:
        index = argv.index("--game-id")
        if index + 1 < len(argv):
            game_id = argv[index + 1]
            argv = argv[:index] + argv[index + 2:]

    args = [a for a in argv if not a.startswith("--")]
    # The URL is OPTIONAL now, and normally absent: a game tile carries
    # --game-id and the launcher resolves the URL from the gateway, falling
    # back to synced.json. Two callers still pass one positionally — a shortcut
    # written before that change, and the website tile, which has no game id to
    # resolve from.
    if len(args) < 2:
        print(__doc__)
        return 2

    app_id, profile = args[0], args[1]
    url = args[2] if len(args) > 2 else ""
    os.makedirs(profile, exist_ok=True)

    log(f"--- launch: game={game_id or '?'} provider={provider or '?'} "
        f"browser={app_id}")

    url, source = resolve_launch(game_id, url, provider)
    log(f"launch URL from {source}")

    if not url:
        log("no launch URL: the gateway did not answer and nothing is stored "
            f"for game {game_id or '?'}. Sync once while the gateway is up.")
        return 4

    # Last gate before a browser opens. resolve_launch already refuses a URL
    # the gateway should not have sent, but the stored fallback reaches here
    # too — written by an earlier sync and sat on disk ever since. Refusing to
    # launch is the right outcome: a game that does not start is a complaint,
    # and a fullscreen page pretending to be a provider sign-in is a stolen
    # account.
    is_page = provider in WEBSITE_PROVIDERS
    if not url_is_acceptable(url, provider):
        log(f"REFUSED: {url!r} is not somewhere a "
            f"{'website tile' if is_page else 'game'} may open "
            f"(source: {source}). Not launching.")
        return 3

    pads = wait_for_gamepad()
    if len(pads) > 1:
        # Known issue: with several controllers connected the browser can bind
        # to the idle virtual pad and report no gamepad at all. Verified on
        # hardware — 4,473 events on the live pad, zero on the one Chrome
        # opened. Logged so it is diagnosable rather than mysterious.
        log(f"{len(pads)} virtual gamepads: {pads}. "
              "If the controller does not work in-game, disconnect the extra one.")
    elif not pads:
        log("no virtual gamepad yet; controller may not work")

    if is_page:
        # Its own profile, its own window, and none of the automation a game
        # gets: no debugging port to drive, no Play button to press, no
        # platform override to hold. A page is just a page.
        profile = website_profile(profile)
        os.makedirs(profile, exist_ok=True)
        log(f"opening a website in its own profile: {profile}")
        code = subprocess.Popen(build_website_command(app_id, profile, url)).wait()
        log(f"--- website closed (browser exit {code})")
        return code

    browser = subprocess.Popen(build_browser_command(
        app_id, profile, url, kiosk,
        user_agent=provider_profile(provider).get("user_agent", "")))

    # Before autoplay, and regardless of it: the OS gate blocks a Play the user
    # presses by hand just as surely as one the launcher presses.
    #
    # `held` is not an unused variable. It is the open socket, and the override
    # lives exactly as long as this reference does.
    held = None
    try:
        held = hold_platform_override(url, provider)
    except Exception as exc:  # never let automation break the launch
        log(f"platform override failed, continuing: {exc}")

    if autoplay:
        try:
            try_autoplay(url, provider)
        except Exception as exc:  # never let automation break the launch
            log(f"autoplay failed, continuing: {exc}")

    try:
        wake_gamepads(url, provider)
    except Exception as exc:
        log(f"gamepad wake failed, continuing: {exc}")

    try:
        log_browser_gamepads(url, provider)
    except Exception as exc:          # diagnostics never break a launch
        log(f"gamepad diagnostic failed, continuing: {exc}")

    started = time.monotonic()
    code = browser.wait()
    if held is not None:
        held.close()
    log(f"--- session ended after {int(time.monotonic() - started)}s "
        f"(browser exit {code})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
