# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Autoplay constants: JS templates, provider profiles, and word lists."""

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

# What the stream actually came down at, beside what the window asked for.
#
# Scaling the interface with --force-device-scale-factor makes a provider's UI
# readable on a handheld and across a room, but it also shrinks the CSS
# viewport: 1280x720 at 1.25 leaves the page believing it is 1024x576. Whether
# that costs picture quality depends on something only the provider knows —
# whether it sizes the stream from CSS pixels or from real ones — and the only
# honest way to find out is to read the video element while a game runs.
#
# Logged, not acted on. If `stream` comes back matching `css` rather than
# `physical`, the scaling is costing resolution and the ladder wants
# revisiting. If it matches `physical`, the interface got bigger for free.
STREAM_STATS_JS = """
(() => {
  const v = [...document.querySelectorAll('video')].find(
    (el) => el.videoWidth > 1 && el.videoHeight > 1);
  if (!v) return null;
  return JSON.stringify({
    stream: v.videoWidth + 'x' + v.videoHeight,
    css: window.innerWidth + 'x' + window.innerHeight,
    physical: Math.round(window.innerWidth * window.devicePixelRatio) + 'x' +
              Math.round(window.innerHeight * window.devicePixelRatio),
    dpr: window.devicePixelRatio,
  });
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
