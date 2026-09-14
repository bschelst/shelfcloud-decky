// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
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

export function Logo({ size = 20 }: { size?: number }) {
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

export default Logo;
