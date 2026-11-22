// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// The Shelfcloud mark, as a component. Inlined because anything fetched from
// the plugin dir is cross-origin inside Steam (steamloopback.host).

// Unique per instance. A fixed gradient id would collide across mounts and
// leave an instance pointing at a definition that already unmounted, which
// then renders black.
let nextId = 0;

// The cloud's own bounds in the 256x256 source, squared off, so `size` means
// about the same here as for the react-icons next to it.
const VIEW_BOX = "30 34 200 200";

/** The mark as one flat colour, for Decky's plugin list. Sized in em so it
 *  takes its colour and size from the row like the icons around it. Shelf bars
 *  are masked out, not painted, becuase the row colour changes when selected. */
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
      {/* One rect masked to the mark, takes the row colour. */}
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

/** The mark for a Steam notification. toaster.toast() takes `logo` as a
 *  ReactNode, so there is no image file to ship (a plugin-local <img> would be
 *  cross-origin). Sized in em: Steam scales notification text with the system
 *  font size, so a fixed pixel size is wrong on a TV. */
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
