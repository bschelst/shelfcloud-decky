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


