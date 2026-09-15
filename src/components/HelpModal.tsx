// Shelfcloud project - https://shelfcloud.schelstraete.org - bart@schelstraete.org
//
// Help and documentation, as a real page over the top of Steam.
//
// Scrolling here is focus-driven. Steam's UI scrolls to keep the *focused*
// element in view, so a wall of plain text cannot be scrolled with a
// controller at all — there is nothing for focus to land on and the stick does
// nothing. Every section is therefore its own Focusable that pulls itself into
// view when focused, and the jump-to buttons give a way past long sections
// without walking through them.

import { ConfirmModal, DialogButton, Focusable } from "@decky/ui";
import { FC, ReactNode, RefObject, useRef } from "react";
import {
  FaBookOpen,
  FaCog,
  FaGamepad,
  FaListUl,
  FaPlay,
  FaShieldAlt,
  FaSyncAlt,
  FaWrench,
} from "react-icons/fa";

import { theme } from "../lib/theme";
import { Logo } from "./Logo";
import { providerInfo } from "../lib/theme";

const SectionHeader: FC<{ icon: ReactNode; title: string }> = ({ icon, title }) => (
  <div style={{
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 12,
    paddingBottom: 8,
    borderBottom: `1px solid ${theme.border}`,
  }}>
    <span style={{ fontSize: 20, color: theme.accent, display: "flex" }}>{icon}</span>
    <span style={{ fontSize: 16, fontWeight: "bold" }}>{title}</span>
  </div>
);

const HelpSection: FC<{ children: ReactNode; sectionRef?: RefObject<HTMLDivElement | null> }> = ({
  children,
  sectionRef,
}) => (
  <div ref={sectionRef} style={{
    padding: 16,
    marginBottom: 16,
    background: theme.surface,
    borderRadius: 8,
  }}>
    {children}
  </div>
);

const TocButton: FC<{ icon: ReactNode; label: string; onClick: () => void }> = ({
  icon, label, onClick,
}) => (
  <DialogButton
    onClick={onClick}
    style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "8px 12px",
      minWidth: "auto",
      fontSize: 12,
    }}
  >
    {icon}
    <span>{label}</span>
  </DialogButton>
);

/** Wraps a section so moving focus onto it scrolls it into view. */
const Scrolls: FC<{ children: ReactNode }> = ({ children }) => (
  <Focusable
    onFocus={(e: React.FocusEvent<HTMLDivElement>) =>
      e.target.scrollIntoView({ behavior: "smooth", block: "nearest" })
    }
  >
    {children}
  </Focusable>
);

const Step: FC<{ n: number; children: ReactNode }> = ({ n, children }) => (
  <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
    <div style={{
      flex: "0 0 auto",
      width: 20,
      height: 20,
      borderRadius: 10,
      background: theme.accentSoft,
      color: theme.accentText,
      fontSize: 11,
      fontWeight: 700,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
    }}>
      {n}
    </div>
    <div style={{ flex: 1 }}>{children}</div>
  </div>
);

const LIST: React.CSSProperties = { marginTop: 8, paddingLeft: 20, lineHeight: 1.6 };

interface HelpModalProps {
  closeModal?: () => void;
  version: string;
}

const HelpModal: FC<HelpModalProps> = ({ closeModal, version }) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const aboutRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<HTMLDivElement>(null);
  const gamesRef = useRef<HTMLDivElement>(null);
  const shelvesRef = useRef<HTMLDivElement>(null);
  const playingRef = useRef<HTMLDivElement>(null);
  const syncRef = useRef<HTMLDivElement>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const troubleRef = useRef<HTMLDivElement>(null);
  const privacyRef = useRef<HTMLDivElement>(null);

  const jump = (ref: RefObject<HTMLDivElement | null>) => {
    if (ref.current && scrollContainerRef.current) {
      ref.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <ConfirmModal
      strTitle="Shelfcloud — Help & documentation"
      strOKButtonText="Close"
      onOK={closeModal}
      onCancel={closeModal}
      bHideCloseIcon={false}
    >
      {/* Jump-to-section */}
      <div style={{
        marginBottom: 16,
        padding: 12,
        background: theme.surfaceRaised,
        borderRadius: 8,
      }}>
        <div style={{
          fontSize: 12,
          opacity: 0.7,
          marginBottom: 8,
          textTransform: "uppercase",
          letterSpacing: 1,
        }}>
          Jump to section
        </div>
        <Focusable style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          <TocButton icon={<Logo size={16} />} label="About" onClick={() => jump(aboutRef)} />
          <TocButton icon={<FaPlay size={14} />} label="Getting started" onClick={() => jump(startRef)} />
          <TocButton icon={<FaListUl size={14} />} label="Adding games" onClick={() => jump(gamesRef)} />
          <TocButton icon={<FaBookOpen size={14} />} label="Shelves" onClick={() => jump(shelvesRef)} />
          <TocButton icon={<FaGamepad size={14} />} label="Playing" onClick={() => jump(playingRef)} />
          <TocButton icon={<FaSyncAlt size={14} />} label="Syncing" onClick={() => jump(syncRef)} />
          <TocButton icon={<FaCog size={14} />} label="Settings" onClick={() => jump(settingsRef)} />
          <TocButton icon={<FaWrench size={14} />} label="Troubleshooting" onClick={() => jump(troubleRef)} />
          <TocButton icon={<FaShieldAlt size={14} />} label="Privacy" onClick={() => jump(privacyRef)} />
        </Focusable>
      </div>

      <div
        ref={scrollContainerRef}
        style={{ maxHeight: "50vh", overflow: "auto", scrollBehavior: "smooth" }}
      >
        <Focusable style={{ padding: 4 }}>

          <Scrolls>
            <HelpSection sectionRef={aboutRef}>
              <SectionHeader icon={<Logo size={18} />} title="About Shelfcloud" />
              <p style={{ lineHeight: 1.6, marginBottom: 8 }}>
                Cloud games do not appear in your Steam library on their own.
                Shelfcloud puts them there: you select games on the Shelfcloud
                website — from any device — and they show up here as normal
                Steam tiles with artwork. Launching one opens the provider and
                goes straight into that game.
              </p>
              <p style={{ lineHeight: 1.6 }}>Supported services:</p>
              <ul style={LIST}>
                {[
                  ["boosteroid", "streams games you already own"],
                  ["geforce_now", "streams games you already own"],
                  ["xbox_cloud", "the subscription is the catalogue"],
                  ["amazon_luna", "the subscription is the catalogue"],
                ].map(([key, note]) => {
                  const p = providerInfo(key);
                  return (
                    <li key={key} style={{ marginTop: 4 }}>
                      <strong style={{ color: p.color }}>{p.label}</strong>
                      <span style={{ opacity: 0.75 }}> — {note}</span>
                    </li>
                  );
                })}
              </ul>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                The distinction matters. Boosteroid and GeForce NOW play games
                you already own. On <strong>Xbox Cloud</strong> and{" "}
                <strong>Amazon Luna</strong> the subscription <em>is</em> the
                catalogue — owning a game on Steam grants nothing there — so
                both stay <strong>off until you switch them on</strong> under
                My Services on the website. That way a tile never opens a
                paywall you did not ask for.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                Both also sell several plans with different games on each
                (Game Pass Essential, Premium or Ultimate; Luna with Prime or
                Luna Premium). Say which one you have on the website and games
                above it are marked before you add them, rather than failing at
                the paywall afterwards.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8, opacity: 0.8 }}>
                Shelfcloud does not buy or subscribe to anything. You need
                whatever access each game already requires.
              </p>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={startRef}>
              <SectionHeader icon={<FaPlay />} title="Getting started" />
              <Step n={1}>
                Press <strong>Sign in with Steam</strong>. Shelfcloud opens; sign
                in there. This panel notices on its own — you do not need to come
                back and press anything.
              </Step>
              <Step n={2}>
                Under <strong>Settings</strong>, choose the browser to play in
                (Chrome or Edge). If none is installed, the plugin offers to
                install one for you and sets up gamepad permissions automatically.{" "}
                <span style={{ color: theme.warn }}>Firefox is not supported</span> —
                cloud providers need the video codecs and gamepad APIs that only
                Chromium browsers have.
              </Step>
              <Step n={3}>
                Select some games on Shelfcloud, then press <strong>Sync now</strong>.
              </Step>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={gamesRef}>
              <SectionHeader icon={<FaListUl />} title="Adding games" />
              <ul style={LIST}>
                <li>
                  <strong>Select games on Shelfcloud</strong> — opens the catalogue,
                  where you can search and choose what appears here.
                </li>
                <li style={{ marginTop: 8 }}>
                  <strong>Add all my games</strong> — reads which games you already
                  own on Steam and adds every one that also runs on a cloud service
                  you have. The number on the button is how many are waiting.
                </li>
              </ul>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={shelvesRef}>
              <SectionHeader icon={<FaBookOpen />} title="Shelves" />
              <p style={{ lineHeight: 1.6, marginBottom: 8 }}>
                A <strong>shelf</strong> is a named collection of games you
                create on the Shelfcloud website. Each device can be pointed at
                a different shelf, so you can tailor what appears on each machine.
              </p>
              <ul style={LIST}>
                <li>
                  <strong>Shared library</strong> — the default. Every device
                  sees these games.
                </li>
                <li style={{ marginTop: 6 }}>
                  <strong>A shelf</strong> — only devices pointed at it see its
                  games (plus the shared ones).
                </li>
              </ul>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                Pick which shelf this device uses under{" "}
                <strong>Settings &gt; Shelf</strong>. Create and manage shelves
                on the website.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8, opacity: 0.8 }}>
                If you only have one device, you do not need shelves — the shared
                library is all you need.
              </p>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={playingRef}>
              <SectionHeader icon={<FaGamepad />} title="Playing a game" />
              <p style={{ lineHeight: 1.6 }}>
                Launch the tile like any Steam game. The browser opens and
                Shelfcloud presses Play for you.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                <strong>The first time on each service you must sign in yourself.</strong>{" "}
                Shelfcloud never sees those credentials — the browser keeps them in
                its own profile, so it is a one-off per service, not per game.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8, opacity: 0.8 }}>
                Hold the Steam button and use the right stick as a mouse if a page
                needs a click Shelfcloud did not make.
              </p>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={syncRef}>
              <SectionHeader icon={<FaSyncAlt />} title="Keeping in sync" />
              <p style={{ lineHeight: 1.6 }}>
                The plugin checks for changes on its own — how often is up to you
                under <strong>Sync every</strong>. Games added on Shelfcloud appear
                here without you doing anything, and a notification names them.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                Remove a game on Shelfcloud and its tile disappears at the next
                sync. Tiles you made yourself are never touched.
              </p>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={settingsRef}>
              <SectionHeader icon={<FaCog />} title="Settings" />
              <ul style={LIST}>
                <li><strong>Browser</strong> — which browser games play in.</li>
                <li style={{ marginTop: 6 }}>
                  <strong>Shelf</strong> — which shelf this device syncs.
                  "Shared games" is the default; choose a named shelf to get a
                  different game set on this device.
                </li>
                <li style={{ marginTop: 6 }}>
                  <strong>Sync every</strong> — how often to check for changes. More
                  often is not better; every few minutes is plenty.
                </li>
                <li style={{ marginTop: 6 }}>
                  <strong>Fix gamepad permissions</strong> — re-applies the flatpak
                  sandbox overrides that let the browser see your controller. Done
                  automatically on startup and after a browser install, but
                  available here if something went wrong.
                </li>
                <li style={{ marginTop: 6 }}>
                  <strong style={{ color: theme.bad }}>Remove all games</strong> —
                  deletes every tile Shelfcloud created. It asks twice. Your choices
                  on Shelfcloud are kept, so a sync brings them all back.
                </li>
                <li style={{ marginTop: 6 }}>
                  <strong>Sign out</strong> — forgets this device's credential.
                  Existing tiles stay, but stop updating.
                </li>
              </ul>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={troubleRef}>
              <SectionHeader icon={<FaWrench />} title="If something is wrong" />
              <ul style={LIST}>
                <li>
                  <strong>Nothing appears after syncing.</strong> Check you are
                  signed in — the panel says so at the top — and that you have
                  selected games on Shelfcloud.
                </li>
                <li style={{ marginTop: 8 }}>
                  <strong>A game opens the wrong page, or asks you to log in.</strong>{" "}
                  Sign in on that provider once, in the browser Shelfcloud uses. The
                  profile is kept, so it should only happen once.
                </li>
                <li style={{ marginTop: 8 }}>
                  <strong>A tile does nothing.</strong> Every launch is logged at{" "}
                  <span style={{ opacity: 0.85, wordBreak: "break-all" }}>
                    ~/homebrew/logs/shelfcloud-launch.log
                  </span>
                  , including why it did what it did.
                </li>
                <li style={{ marginTop: 8 }}>
                  <strong>Controller not detected in the stream.</strong>{" "}
                  Go to <strong>Settings &gt; Fix gamepad permissions</strong>.
                  This grants the browser access to input devices inside its
                  flatpak sandbox.
                </li>
                <li style={{ marginTop: 8 }}>
                  <strong style={{ color: theme.warn }}>
                    "Could not reach the gateway".
                  </strong>{" "}
                  The Shelfcloud server is asleep or off the network. Games still
                  start from their last known link, so this is rarely fatal.
                </li>
              </ul>
            </HelpSection>
          </Scrolls>

          <Scrolls>
            <HelpSection sectionRef={privacyRef}>
              <SectionHeader icon={<FaShieldAlt />} title="What is recorded" />
              <p style={{ lineHeight: 1.6 }}>
                When you launch a game the server notes which game, when, and which
                service — so the catalogue can show what people actually play, and
                so your link can be refreshed if a provider changes it.
              </p>
              <p style={{ lineHeight: 1.6, marginTop: 8 }}>
                It does not record what you browse, and it never stores your
                provider passwords — those stay in your browser and are never sent
                anywhere by this plugin.
              </p>
              <div style={{
                marginTop: 12,
                paddingTop: 12,
                borderTop: `1px solid ${theme.border}`,
                fontSize: 12,
                opacity: 0.7,
              }}>
                <div>Plugin version {version}</div>
              </div>
            </HelpSection>
          </Scrolls>

        </Focusable>
      </div>
    </ConfirmModal>
  );
};

export default HelpModal;
