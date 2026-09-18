// Shelfcloud Gamepad Shim
//
// Chrome hides gamepads from navigator.getGamepads() until a physical button
// press (W3C fingerprinting mitigation). Cloud providers check on page load
// and fall back to "use your phone" when the list is empty.
//
// This overrides getGamepads to return a virtual Xbox 360 pad in slot 0
// until the real API unlocks. Once Chrome exposes the real pads, the
// override passes through transparently.

(() => {
  const real = navigator.getGamepads.bind(navigator);
  const fakeBtn = () => ({ pressed: false, touched: false, value: 0 });
  const fakePad = {
    id: "Xbox 360 Controller (XInput STANDARD GAMEPAD)",
    index: 0,
    connected: true,
    mapping: "standard",
    axes: [0, 0, 0, 0],
    buttons: Array.from({ length: 17 }, fakeBtn),
    timestamp: performance.now(),
    hapticActuators: [],
    vibrationActuator: null,
  };

  navigator.getGamepads = function () {
    const pads = real();
    if (pads && [...pads].some(Boolean)) return pads;
    return [fakePad, null, null, null];
  };

  window.dispatchEvent(
    new GamepadEvent("gamepadconnected", { gamepad: fakePad })
  );
})();
