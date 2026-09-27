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

# Play words per language. xbox.com localises by IP, so an english-only list
# silently fails: a Belgian browser gets "NU SPELEN", not "PLAY". Single words
# only, matched inside the button's first line, shared by every provider.
PLAY_WORDS = [
    # English, Dutch, French, German, Spanish, Italian, Portuguese
    "play", "spelen", "speel", "jouer", "spielen", "jugar", "juega",
    "gioca", "giocare", "jogar", "jogue",
    # Nordic, both infinitive and imperative (Xbox uses "Spill nå", not "Spille")
    "spela", "spille", "spill", "spil", "pelaa",
    # Central and Eastern Europe
    "graj", "zagraj", "hrát", "hrat", "hrať", "joacă", "joaca", "játék",
    "играть", "играй", "грати", "igraj",
    # Turkish, Greek
    "oyna", "παίξε", "παιξε",
    # CJK — matched as substrings, since these scripts have no word breaks
    "遊ぶ", "プレイ", "开始游戏", "立即游戏", "玩", "플레이", "게임하기",
]

# Words that disqualify a control whatever else it says. Boosteroid's
# "Subscribe and Play" carries a play word but takes money, and clicking the
# wrong thing can start a purchase. Any of these anywhere in the label rejects.
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

# Phrases that name a category, not a "play" action. "Free to Play" is a genre
# filter on GeForce NOW: each word is fine on its own, only the phrase catches
# it. Checked with hyphens turned into spaces, so one entry covers both spellings.
DENY_PHRASES = [
    "free to play", "play to earn", "pay to play", "cross play",
    "couch play", "local play", "online play", "co op play",
    "play store", "google play", "play pass", "play together",
    "how to play", "ways to play", "learn to play", "ready to play",
    "gratis spelen", "samen spelen", "hoe te spelen",
    # Luna's top nav is a <div role=button>, so it lands in the button group.
    # "Remote Play" matched on "play" and took us away from the game. English
    # on every market.
    "remote play",
    # Signed out, Luna shows "Play free with Prime" which goes to a signup. Third
    # layer of defence, the signed-out check and :not(_log_in) also stop it.
    "free with prime", "gratis met prime",
]

# Is the game really running yet? Not just "is there a <video>": Luna's page has
# a paused 0x0 trailer, so that check said yes before we clicked anything. A real
# stream is playing and has real width and height.
IS_STREAMING_JS = """
(() => {
  if (location.href.includes('streaming')) return true;
  return [...document.querySelectorAll('video')].some(
    (v) => !v.paused && !v.ended && v.videoWidth > 1 && v.videoHeight > 1);
})()
"""

# The real stream size next to what the window asked for.
# --force-device-scale-factor makes the UI bigger but shrinks the CSS viewport
# (1280x720 at 1.25 reads as 1024x576), and only the provider knows if that
# costs quality. We only log it: `css` means we lose resolution, `physical`
# means the bigger UI was free.
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

# What the page had, logged when nothing matched. Providers change their Play
# button without warning, and a bare "not found" means a trip to the device.
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

# Provider profiles. The signed_out checks look at the URL or the DOM, not the
# text on purpose: the wording is localised, but the login URL and routing are not.
PROVIDERS = {
    "boosteroid": {
        "label": "Boosteroid",
        "signed_out": "!!document.querySelector('a[href*=\"/login\"], input[type=password]')",
        "selectors": [],
        "setup_url": "https://cloud.boosteroid.com/",
    },
    "xbox_cloud": {
        "label": "Xbox Cloud",
        # signed out shows /auth/msa and no Play control. Locale prefix varies.
        "signed_out": "!!document.querySelector('a[href*=\"/auth/msa\"]')",
        # signed in, the Play control links into the launch route.
        "selectors": ["a[href*='/play/launch/']"],
        "setup_url": "https://www.xbox.com/play",
    },
    "geforce_now": {
        # the deeplink jumps to #/loginwall when not signed in, so no DOM check.
        "label": "GeForce NOW",
        "signed_out": "location.hash.includes('loginwall')",
        "selectors": ["button[aria-label*='Play' i]"],
        "setup_url": "https://play.geforcenow.com/",
    },
    "amazon_luna": {
        "label": "Amazon Luna",
        # Luna refuses to play on Linux, but the browser can do it: it has the
        # codecs, MediaSource and EME. Luna reads the platform from the
        # User-Agent alone, so faking the UA string is enough (it ignores
        # navigator.userAgentData). Same Edge version we really are, only the
        # platform is different.
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 Edg/153.0.0.0"
        ),
        # saying we are Windows makes Luna poke "luna://" to look for a desktop
        # app, which Chromium shows as an xdg-open prompt. See below.
        "startup_js": "NEUTRALISE_APP_HANDOFF_JS",
        # Luna sends the launch URL to the viewer's own country: luna.amazon.de,
        # luna.amazon.co.uk, luna.amazon.com.be. Matching the full host only
        # works in some markets, so we match the "luna.amazon." start.
        "page_match": "luna.amazon.",
        # Element ids, not text: Luna localises by IP, a Belgian browser gets
        # "Inloggen". This check matters most, becaue signed out Luna's main
        # button "Speel gratis met Prime" has a play word and goes to Prime.
        "signed_out": (
            "!!document.querySelector('#item_secondary_nav_button_sign_in, "
            "[data-test-id=\"primary_entitled_button_log_in\"]')"
        ),
        # Both states share the same prefix and differ only in the suffix:
        #   ..._purchase_detail  signed in  -> "Play now"
        #   ..._log_in           signed out -> "Play free with Prime"
        # The :not(_log_in) is the important half: without it an expired session
        # would press the Prime signup button.
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
