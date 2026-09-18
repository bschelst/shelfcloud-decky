// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The Shelfcloud mark, as a component.
//
// Inlined rather than loaded from a file. This bundle runs inside Steam's
// client at https://steamloopback.host, where anything fetched from the plugin
// directory is cross-origin — the same rule that forced artwork through the
// Python backend. At 1.3 KB the mark costs less inlined than the machinery to
// serve it would.

/** Unique per instance.
 *
 *  The gradient is referenced by `url(#id)`, and ids are global to the
 *  document. Steam's UI is one long-lived page with Decky's panels mounting
 *  and unmounting inside it, so a fixed id means the second instance's
 *  gradient definition wins for BOTH — and when that instance unmounts, the
 *  first one is left pointing at a definition that no longer exists and
 *  silently renders black.
 */
let nextId = 0;

/** The artwork's own bounds inside the 256x256 source, squared off.
 *
 *  The cloud spans x 30..230 and y 56..212, so the source box is ~39% empty
 *  space. Rendering it as-is means `size={20}` puts about 12px of visible
 *  cloud on screen, and next to react-icons — which fill their box — the mark
 *  reads as undersized no matter what number the caller passes.
 *
 *  Cropping here rather than asking every call site to over-size means `size`
 *  means roughly the same thing for this icon as for every other one. The crop
 *  stays square so it can sit in the same slots, and keeps a little padding so
 *  the cloud is not flush against the edge.
 */
const VIEW_BOX = "30 34 200 200";

/** The mark as a single-colour glyph, for Decky's plugin list.
 *
 *  The coloured mark is wrong in that row. Every neighbour — Tailscale,
 *  ProtonDB, SteamGridDB, CSS Loader, Deskflow — is a monochrome react-icon
 *  that takes its colour from the row, turning white when the row is selected.
 *  A filled blue badge sitting among them does not read as "one more plugin",
 *  it reads as a logo someone pasted in.
 *
 *  Sized in `em`, not pixels, for the same reason: react-icons default to
 *  1em, so Decky's own row styling decides how big they are. Passing a pixel
 *  size is what made this one visibly larger than the rest.
 *
 *  The shelf bars are punched out through a mask rather than painted in a
 *  background colour — the row's background changes when it is selected, so
 *  anything painted would be a guess that is wrong half the time.
 */
export function LogoMono() {
  const maskId = `shelfcloud-mono-${nextId++}`;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={VIEW_BOX}
      width="1em"
      height="1em"
      fill="currentColor"
      role="img"
      aria-label="Shelfcloud"
      style={{ display: "block", flex: "none" }}
    >
      <mask id={maskId}>
        {/* White keeps, black removes. */}
        <g fill="#fff">
          <rect x="50" y="130" width="158" height="82" rx="26" />
          <circle cx="66" cy="166" r="36" />
          <circle cx="190" cy="170" r="40" />
          <circle cx="110" cy="108" r="52" />
          <circle cx="168" cy="126" r="42" />
        </g>
        <g fill="#000">
          <rect x="66" y="150" width="124" height="11" rx="5.5" />
          <rect x="66" y="186" width="124" height="11" rx="5.5" />
          <rect x="84" y="124" width="13" height="26" rx="3.5" />
          <rect x="103" y="130" width="13" height="20" rx="3.5" />
          <rect x="122" y="120" width="13" height="30" rx="3.5" />
          <rect x="141" y="128" width="13" height="22" rx="3.5" />
          <rect x="160" y="132" width="13" height="18" rx="3.5" />
          <rect x="93" y="162" width="13" height="24" rx="3.5" />
          <rect x="112" y="166" width="13" height="20" rx="3.5" />
          <rect x="131" y="160" width="13" height="26" rx="3.5" />
          <rect x="150" y="167" width="13" height="19" rx="3.5" />
        </g>
      </mask>
      {/* One rect, masked to the mark. Inherits the row's colour, so it turns
          white on selection exactly like its neighbours. */}
      <rect x="30" y="34" width="200" height="200" mask={`url(#${maskId})`} />
    </svg>
  );
}

export function Logo({ size = 20 }: { size?: number | string }) {
  const gradientId = `shelfcloud-mark-${nextId++}`;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox={VIEW_BOX}
      width={size}
      height={size}
      role="img"
      aria-label="Shelfcloud"
      style={{ display: "block", flex: "none" }}
    >
      <defs>
        <linearGradient
          id={gradientId}
          x1="30" y1="50" x2="226" y2="214"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#8ec5ff" />
          <stop offset="1" stopColor="#4d9fff" />
        </linearGradient>
      </defs>
      <g fill={`url(#${gradientId})`}>
        <rect x="50" y="130" width="158" height="82" rx="26" />
        <circle cx="66" cy="166" r="36" />
        <circle cx="190" cy="170" r="40" />
        <circle cx="110" cy="108" r="52" />
        <circle cx="168" cy="126" r="42" />
      </g>
      <g fill="#0f1621">
        <rect x="66" y="150" width="124" height="11" rx="5.5" />
        <rect x="66" y="186" width="124" height="11" rx="5.5" />
        <rect x="84" y="124" width="13" height="26" rx="3.5" />
        <rect x="103" y="130" width="13" height="20" rx="3.5" />
        <rect x="122" y="120" width="13" height="30" rx="3.5" />
        <rect x="141" y="128" width="13" height="22" rx="3.5" />
        <rect x="160" y="132" width="13" height="18" rx="3.5" />
        <rect x="93" y="162" width="13" height="24" rx="3.5" />
        <rect x="112" y="166" width="13" height="20" rx="3.5" />
        <rect x="131" y="160" width="13" height="26" rx="3.5" />
        <rect x="150" y="167" width="13" height="19" rx="3.5" />
      </g>
    </svg>
  );
}

/** The mark as it should appear on a Steam notification.
 *
 *  toaster.toast() takes `logo` as a ReactNode, NOT a path or a URL — which is
 *  the whole reason this works. An <img> pointing into the plugin directory
 *  would not load: this bundle runs at https://steamloopback.host, so a
 *  plugin-local file is cross-origin, the same rule that forced artwork
 *  through the Python backend. There is no image file to ship and none to get
 *  wrong.
 *
 *  Sizing follows Steam's own notification layout rather than a guess: a
 *  container fixed at 36px with the mark at 1em inside it. The em is what
 *  matters — Steam scales notification text with the system font size, so a
 *  hard pixel size is right on one Deck and wrong on a TV. 36px is the base
 *  the notification logo slot is built around (~40-48px including padding).
 *
 *  Logo rather than LogoMono: a notification is not a list row. Nothing here
 *  recolours on selection, and the coloured mark is what makes a Shelfcloud
 *  toast recognisable at a glance among Steam's own.
 */
export function NotificationLogo() {
  return (
    <div
      style={{
        position: "relative",
        width: "1em",
        height: "1em",
        fontSize: "36px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Logo size="1em" />
    </div>
  );
}

export default Logo;
