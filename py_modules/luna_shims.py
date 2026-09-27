# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""JavaScript shims injected into Luna's page via CDP startup scripts"""

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


REINDEX_GAMEPAD_JS = """
// Steam Input gives the browser a virtual Xbox pad, but it lands at gamepad
// slot 1 with slot 0 empty. Boosteroid, GeForce NOW and Xbox Cloud read slot 0
// only, so the controller does nothing even though the pad is there and its
// buttons register.
//
// This does NOT fake a pad (that broke real detection before, see
// VIRTUAL_GAMEPAD_JS). It only re-shows the REAL live pad at slot 0 when slot 0
// is empty, so the player finds it where it looks. Runs at document start, so
// the player sees it from the first scan.
(() => {
  if (window.__shelfcloudGamepadReindex) return;   // once is enough
  window.__shelfcloudGamepadReindex = true;
  try {
    const orig = navigator.getGamepads
      ? navigator.getGamepads.bind(navigator) : null;
    if (!orig) return;

    const firstReal = (pads) => {
      for (let i = 0; i < pads.length; i++) {
        if (pads[i] && pads[i].connected) return pads[i];
      }
      return null;
    };

    navigator.getGamepads = function() {
      let pads;
      try { pads = orig(); } catch (e) { return orig(); }
      if (!pads) return pads;
      const list = Array.prototype.slice.call(pads);
      if (!list[0]) {
        const real = firstReal(list);
        if (real) list[0] = real;    // the live pad itself, never a copy
      }
      return list;
    };

    // A player that binds on the event, not by polling, needs a nudge too. When
    // a real pad connects somewhere other than slot 0 and slot 0 is still empty,
    // we fire the connect event once more with the REAL pad. Handlers that react
    // by calling getGamepads() then re-scan through the wrap above and find it at
    // slot 0. A flag stops the event from feeding itself.
    let bouncing = false;
    window.addEventListener('gamepadconnected', function(ev) {
      if (bouncing) return;
      try {
        const g = ev.gamepad;
        if (!g || g.index === 0) return;
        const pads = orig();
        if (pads && pads[0]) return;         // slot 0 taken, nothing to do
        bouncing = true;
        try {
          window.dispatchEvent(new GamepadEvent('gamepadconnected', {gamepad: g}));
        } finally {
          bouncing = false;
        }
      } catch (e) { /* a listener must never throw */ }
    }, true);
  } catch (e) { /* best effort, never break the page */ }
})();
"""


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


