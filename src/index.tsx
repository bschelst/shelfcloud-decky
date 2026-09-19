// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import { addEventListener, definePlugin, removeEventListener, toaster } from "@decky/api";
import {
  ButtonItem,
  Dropdown,
  Field,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  FaCog,
  FaGithub,
  FaGlobe,
  FaInfoCircle,
  FaLink,
  FaQuestionCircle,
  FaGamepad,
  FaSignOutAlt,
  FaSteam,
  FaTrashAlt,
} from "react-icons/fa";

import {
  forgetShortcut,
  addOwnedGames,
  clearLibrary,
  dismissInstall,
  getDesiredLibrary,
  installBrowser,
  previewOwned,
  syncSteamLibrary,
  getShelves,
  getStatus,
  logout,
  pollLogin,
  recordShortcut,
  setSetting,
  setShelf,
  startLogin,
  syncFinished,
  applyOverrides,
} from "./lib/api";
import { syncLibrary } from "./lib/sync";
import { changeSummary, syncSummary } from "./lib/summary";
import { LibrarySummary } from "./components/LibrarySummary";
import { Logo, LogoMono, NotificationLogo } from "./components/Logo";
import { Collapsible, IconLabel } from "./components/Collapsible";
import { NoBrowser } from "./components/NoBrowser";
import { finishJob, getJob, setJob, subscribeJob } from "./lib/job";
import { theme } from "./lib/theme";
import type { DesiredGame, InstallState, Status, SyncResult } from "./lib/types";

const SYNC_EVENT = "shelfcloud/library";
const LOGIN_POLL_MS = 2500;
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const GATEWAY_URL = "https://shelfcloud.cc";
const GITHUB_URL = "https://github.com/bschelst/shelfcloud-decky";

/** The shelf picker's stand-in for "no shelf", which the gateway spells null.
 *
 * A Dropdown option needs a key and null is not one. Negative because shelf
 * ids come from a Postgres identity column and start at 1: 0 would merely be
 * unlikely to collide, -1 cannot. A collision would be quiet and wrong — the
 * shelf holding that id would read as "Shared games" and, when chosen, move
 * the device to shared instead of to it.
 */
const SHARED = -1;

/** How often to re-read the library count while a sync is running.
 *
 *  Fast enough that the number visibly climbs, slow enough that it is not a
 *  call per tile: a large sync writes several games a second, so a shorter
 *  interval would buy nothing a reader could perceive. */
const COUNT_POLL_MS = 1500;

/** No install started. Mirrors IDLE_INSTALL in main.py so the panel can read
 *  the fields without guarding each one when the backend predates this. */
const IDLE_INSTALL: InstallState = {
  running: false, app_id: null, label: null, message: "", error: null, done: false,
};

/** Start a browser install and show it immediately.
 *
 *  The backend returns as soon as the download has started and reports
 *  progress through get_status, so the panel refreshes once here to pick up
 *  `running: true` — that is what starts the progress poll, and what stops the
 *  buttons being pressable twice.
 */
async function startBrowserInstall(appId: string): Promise<Status | null> {
  const started = await installBrowser(appId);
  if (!started.ok) {
    console.error("[shelfcloud] could not start install", started.error);
  }
  return getStatus().catch(() => null);
}

/** Scroll the panel back to the very top.
 *
 * Steam's Quick Access menu scrolls only to whatever has focus, so anything
 * that cannot take focus cannot be scrolled to. The header — the count, the
 * bar and the provider chips — is not interactive, and it sits above every
 * control, so once you scrolled down to Links or About it went off the top and
 * pushing up could not bring it back: focus stopped at the first button, and
 * Steam scrolled no further than that button needed.
 *
 * Wrapping the header in a Focusable ought to be enough on its own, and it is
 * there. This is the part that does not depend on Steam choosing to navigate
 * to a Focusable with nothing to activate: when focus reaches the topmost
 * control, scroll its container fully to the top, which puts the header back
 * on screen regardless.
 */
function scrollPanelToTop(event: React.FocusEvent<HTMLDivElement>): void {
  let node: HTMLElement | null = event.currentTarget;
  while (node) {
    const style = window.getComputedStyle(node);
    const scrolls = style.overflowY === "auto" || style.overflowY === "scroll";
    if (scrolls && node.scrollHeight > node.clientHeight + 8) {
      node.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    node = node.parentElement;
  }
}

/** Open a URL in Steam's built-in browser, the only browser Game Mode has.
 *
 * The cast works around a name collision: `SteamClient.URL` resolves to the
 * DOM's global `URL` type rather than Steam's, so ExecuteSteamURL is invisible
 * to TypeScript even though it is declared by @decky/ui and present at runtime
 * (verified on hardware).
 */
type SteamUrlApi = { ExecuteSteamURL(url: string): void };

function openInSteamBrowser(url: string): void {
  try {
    (SteamClient.URL as unknown as SteamUrlApi).ExecuteSteamURL(`steam://openurl/${url}`);
  } catch (error) {
    console.error("[shelfcloud] could not open browser", error);
    Navigation.NavigateToExternalWeb(url);   // last resort
  }
}

/** The sync currently running, if any. Module scope on purpose: the panel and
 *  the background poll are different callers and must share it. */
let syncInFlight: Promise<SyncOutcome> | null = null;

/** `total` is every entry in the Shelfcloud library, INCLUDING the skipped
 *  ones, so that "N games — X added (Y skipped)" adds up on screen. It used to
 *  be the usable count, which made the two numbers look like they belonged to
 *  the same total when they did not: 385 games, 385 added, 7 skipped invites
 *  the reader to look for the 7 inside the 385, and they are not there. */
type SyncOutcome = { result: SyncResult; total: number; skipped: number };


/** Run a full reconcile, but never two at once.
 *
 *  Without this, two overlapping runs each read the tracked list before either
 *  records anything, so both create shortcuts for the same games and the user
 *  gets duplicate tiles. That is not a rare race: adding 200 games on the
 *  website makes the first sync take minutes while the poll keeps firing every
 *  minute.
 *
 *  A caller arriving mid-sync gets the running one rather than being refused,
 *  so "Sync now" still reports a real outcome. Anything changed after this run
 *  started is picked up by the next poll.
 */
async function runSync(
  onProgress?: (message: string) => void,
): Promise<SyncOutcome & { owned: boolean }> {
  const running = syncInFlight;
  if (running) {
    // Wait for the run already in progress and report its outcome, but mark
    // that this caller did not perform it. Every waiter resolves from the same
    // promise, so without `owned` each one would fire its own notification —
    // six poll ticks during one long sync would mean six identical toasts for
    // a single batch of games.
    return { ...(await running), owned: false };
  }

  const started = performSync(onProgress);
  syncInFlight = started;
  try {
    return { ...(await started), owned: true };
  } finally {
    syncInFlight = null;
  }
}

/** Is this entry complete enough to build a Steam shortcut from?
 *
 *  The backend validates too, but this is the last step before calling
 *  SteamClient, and a half-formed entry there produces a broken tile rather
 *  than an error anyone would notice. Cheap to check, expensive to skip.
 */
function isUsableGame(game: unknown): game is DesiredGame {
  const g = game as DesiredGame;
  return !!g && typeof g === "object"
    && typeof g.game_id === "number"
    && typeof g.name === "string" && g.name.trim() !== ""
    && typeof g.provider === "string" && g.provider !== ""
    && typeof g.exe === "string" && g.exe !== ""
    && typeof g.options === "string" && g.options !== "";
}

async function performSync(onProgress?: (message: string) => void): Promise<SyncOutcome> {
  const library = await getDesiredLibrary();
  if (!library.ok) {
    throw new Error(library.error || "Could not read your library");
  }

  // Never hand SteamClient something malformed, whatever the gateway said.
  const all = Array.isArray(library.desired) ? library.desired : [];
  const desired = all.filter(isUsableGame);
  const unusable = all.length - desired.length;
  if (unusable > 0) {
    console.warn(`[shelfcloud] ignoring ${unusable} unusable library entries`);
  }
  const tracked = (library.tracked && typeof library.tracked === "object")
    ? library.tracked : {};

  const result = await syncLibrary(desired, tracked, {
    record: recordShortcut,
    forget: forgetShortcut,
    onProgress,
  });
  await syncFinished(result.added, result.removed, result.updated);
  // Two places drop entries before we get here: the backend, which reports its
  // count as library.skipped, and isUsableGame above. Both are already gone
  // from `desired`, so the library's real size is the survivors plus the two
  // rejected counts -- report that, not the survivors alone.
  const skipped = (library.skipped ?? 0) + unusable;
  return {
    result,
    total: desired.length + skipped,
    skipped,
  };
}

/** Reconcile the Steam library, reporting progress into the job store.
 *
 * At module scope, not inside the component, for the same reason the store is:
 * this must keep running and keep reporting while the panel is closed.
 */
async function doSync(quiet = false): Promise<void> {
  setJob({ busy: true });
  try {
    const { result, total, skipped } = await runSync((line) => {
      // The sync emits "Adding X (12/124)"; reuse it rather than threading a
      // second callback through every call site.
      const match = line.match(/\((\d+)\/(\d+)\)\s*$/);
      const done = match ? Number(match[1]) : 0;
      const all = match ? Number(match[2]) : 0;
      // Keep the last known fraction on lines that carry no counter, so the
      // bar holds its position instead of flicking back to indeterminate.
      setJob({ message: line, progress: match && all > 0 ? done / all : getJob().progress });
    });
    finishJob(syncSummary(total, result, skipped));
    if (!quiet) toaster.toast({ title: "Shelfcloud", body: changeSummary(result),
                               logo: <NotificationLogo /> });
  } catch (error) {
    finishJob(String(error));
    if (!quiet) toaster.toast({ title: "Sync failed", body: String(error),
                               logo: <NotificationLogo /> });
  }
}

/** The "add all my games" run in progress, if any.
 *
 * Single-flight like the sync, and for a sharper reason: the panel can be
 * closed and reopened mid-run, and a reopened panel that had lost its busy
 * flag offered the button again. Pressing it started a second pass over the
 * same Steam library while the first was still writing. Now a second press
 * joins the run already going.
 */
let addOwnedInFlight: Promise<void> | null = null;

async function doAddOwned(): Promise<void> {
  if (addOwnedInFlight) return addOwnedInFlight;

  const started = (async () => {
    setJob({ busy: true, progress: null, message: "Reading your Steam library…" });
    try {
      const steam = await syncSteamLibrary();
      if (!steam.ok) {
        finishJob(steam.message || steam.error || "Could not read your Steam library");
        return;
      }

      setJob({ message: `You own ${steam.owned} Steam games. Adding the cloud ones…` });
      const result = await addOwnedGames();
      if (!result.ok) {
        finishJob(result.error || "Could not add games");
        return;
      }

      setJob({ message: `Added ${result.added} games. Syncing to your library…` });
      await doSync(true);
    } catch (error) {
      finishJob(String(error));
    }
  })();

  addOwnedInFlight = started;
  try {
    await started;
  } finally {
    addOwnedInFlight = null;
  }
}

/** Empty the library and let a sync delete every tile we created. */
async function doRemoveAll(): Promise<void> {
  setJob({ busy: true, progress: null });
  try {
    const result = await clearLibrary();
    if (!result.ok) {
      finishJob(result.error || "Could not clear your library");
      return;
    }
    setJob({ message: `Removed ${result.removed} games. Cleaning up tiles…` });
    await doSync(true);
  } catch (error) {
    finishJob(String(error));
  }
}

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [owned, setOwned] = useState<number | null>(null);
  // Which shelf this device shows, and what it could show. Loaded from the
  // gateway rather than from settings: the shelf can be changed on the website
  // or another device, so a local copy would go quietly out of date.
  const [shelves, setShelves] = useState<Awaited<ReturnType<typeof getShelves>> | null>(null);
  // Two-press confirm: this deletes every tile the plugin made.
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const loginTimer = useRef<number | null>(null);
  // A sign-in that has been STARTED but not yet handed to the browser.
  //
  // The gap exists so the pairing code can be read before Steam's browser
  // takes the screen and closes this panel.
  const [pendingLogin, setPendingLogin] =
    useState<{ url: string; code: string; session: string } | null>(null);
  // "of N playable", held still for the duration of a sync — see below.
  const totalWhenSyncStarted = useRef<number | null>(null);

  // Whatever is running right now, even if it started before this panel was
  // opened. This is what makes a reopened panel show live progress rather than
  // an idle one.
  const { busy, message, progress, completed } =
    useSyncExternalStore(subscribeJob, getJob);

  const refresh = useCallback(async () => {
    setStatus(await getStatus());
    // How many owned games could still be added, so the button can say the
    // number rather than making the user press it to find out.
    const preview = await previewOwned();
    setOwned(preview.ok ? (preview.not_added ?? 0) : null);
  }, []);

  // On mount, and again whenever a job finishes while the panel is open, so
  // the counts follow work that completed with the panel closed.
  useEffect(() => { void refresh(); }, [refresh, completed]);

  // Alongside the status, and re-read after a sync: a sync is when the library
  // changes, which is exactly when a shelf's game count is out of date.
  useEffect(() => {
    void getShelves().then(setShelves).catch(() => setShelves(null));
  }, [completed]);

  // While a sync runs, keep the headline count honest.
  //
  // It is read from getStatus() and was refreshed only when a job FINISHED, so
  // during a long "add all my games" it sat at its pre-sync value — reading 0
  // while the bar advanced and the line underneath said "Adding … (189/385)".
  // The one number people watch was the one number not moving.
  //
  // Polled rather than counted locally: the backend's tracked list is the
  // truth, and the sync adds, updates and removes, so counting steps here
  // would drift from it. Only getStatus() is called, not the owned-games
  // preview, which is a slower query and cannot change mid-sync.
  useEffect(() => {
    if (!busy) {
      totalWhenSyncStarted.current = null;
      return undefined;
    }
    // Freeze the denominator for the duration.
    //
    // It is owned-not-yet-added plus already-tracked, and only the second half
    // is refreshed here — the first is a slower query that cannot change
    // mid-sync. Left alone the total would climb with the count, so adding 189
    // games would read "189 of 581 playable" when the real answer is 392. The
    // number of playable games does not change because you added some.
    if (totalWhenSyncStarted.current === null) {
      totalWhenSyncStarted.current = (owned ?? 0) + (status?.tracked ?? 0);
    }
    const timer = window.setInterval(() => {
      void getStatus().then(setStatus).catch(() => {});
    }, COUNT_POLL_MS);
    return () => window.clearInterval(timer);
    // Deliberately depends only on `busy`. Adding `owned` or `status` would
    // re-run this on every poll, and the frozen total must be the value from
    // the moment the sync began, not whatever it is a tick later.
  }, [busy]);

  // While a browser is downloading, keep reading its progress.
  //
  // Separate from the sync poll above: an install is not a "job" in the store
  // sense — it lives in the backend so it survives the panel closing, and it
  // runs in the one state where the rest of the panel does not exist yet.
  // When it finishes, the same poll picks up the newly detected browser and
  // the panel moves on by itself.
  const installing = status?.install?.running ?? false;
  useEffect(() => {
    if (!installing) return undefined;
    const timer = window.setInterval(() => {
      void getStatus().then(setStatus).catch(() => {});
    }, COUNT_POLL_MS);
    return () => window.clearInterval(timer);
  }, [installing]);

  // Cancel any in-flight login poll when the panel unmounts, so a forgotten
  // timer cannot keep hitting the gateway for five minutes.
  useEffect(() => () => {
    if (loginTimer.current) window.clearInterval(loginTimer.current);
  }, []);

  const sync = useCallback((quiet = false) => doSync(quiet), []);
  const addOwned = useCallback(() => doAddOwned(), []);

  // NOTE: the background sync listener is deliberately NOT registered here.
  // This component only mounts while the panel is open, so a listener here
  // would make "background" sync work only while the user is watching it.
  // It lives in definePlugin() instead, which stays alive for the session.

  const signIn = useCallback(async () => {
    setPendingLogin(null);
    setStatus((prev) => prev ? { ...prev, pending_login_code: "" } : prev);
    setJob({ busy: true, message: "Opening Steam sign-in…" });
    const start = await startLogin();
    if (!start.ok || !start.login_url || !start.session_token) {
      finishJob(start.error || "Could not start sign-in");
      return;
    }

    // Game Mode has no browser of its own, so hand the URL to Steam.
    //
    // Navigation.NavigateToExternalWeb() opens a browser window but leaves it
    // on its blank data:text/html placeholder — verified on hardware. The
    // steam://openurl protocol actually navigates, and is the same mechanism
    // boosteroid-steamos uses for Google sign-in.
    // NOT opened here any more.
    //
    // Opening Steam's browser closes the Quick Access Menu, and the pairing
    // code lives in the Quick Access Menu — so firing this immediately meant
    // the panel vanished before the code had been on screen for a frame. The
    // user then reached a page asking for a code they had never seen, and the
    // only way to find it was to guess that reopening the QAM over the browser
    // would show it.
    //
    // The code is shown first, with a button to continue. Nothing is being
    // memorised under time pressure: it is on screen, the user reads it, and
    // presses Open when ready. The QAM can still be reopened over the browser
    // to check it, because the backend keeps it for the life of the sign-in.
    setPendingLogin({ url: start.login_url, code: start.confirm_code || "",
                      session: start.session_token });
    setJob({ busy: false, message: "" });
  }, [refresh, sync]);

  const openSteamSignIn = useCallback((url: string, session: string) => {
    openInSteamBrowser(url);

    // The backend waits for the sign-in to complete and stores the token, so
    // it finishes even though opening Steam's browser closes this panel and
    // unmounts everything here. This only watches for the result, and losing
    // the watcher no longer loses the sign-in.
    setJob({ busy: false, message: "Finish signing in in the browser…" });

    const deadline = Date.now() + LOGIN_TIMEOUT_MS;
    loginTimer.current = window.setInterval(() => {
      void (async () => {
        try {
          const poll = await pollLogin(session);
          if (poll.ok && poll.status === "signed_in") {
            window.clearInterval(loginTimer.current!);
            finishJob("Signed in!");
            await refresh();
            void sync();
            return;
          }
          if (Date.now() > deadline) {
            window.clearInterval(loginTimer.current!);
            setJob({ message: "Sign-in is taking a while. Reopen this panel to check." });
          }
        } catch (error) {
          console.error("[shelfcloud] login poll failed", error);
        }
      })();
    }, LOGIN_POLL_MS);
  }, [refresh, sync]);

  /** Empty the library and let sync delete every tile we created. */
  const removeAll = useCallback(async () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setJob({ message: "Press again to remove every Shelfcloud game from your library." });
      return;
    }
    setConfirmClear(false);
    await doRemoveAll();
  }, [confirmClear]);

  const signOut = useCallback(async () => {
    if (!confirmSignOut) {
      setConfirmSignOut(true);
      setJob({ message: "Press again to sign out." });
      return;
    }
    setConfirmSignOut(false);
    await logout();
    setJob({ message: "Signed out. Your existing tiles were left alone." });
    void refresh();
  }, [confirmSignOut, refresh]);

  if (!status) {
    return <PanelSection title="Shelfcloud"><PanelSectionRow>Loading…</PanelSectionRow></PanelSection>;
  }


  if (!status.browsers.length) {
    return (
      <NoBrowser
        installable={status.installable ?? []}
        install={status.install ?? IDLE_INSTALL}
        onInstall={(appId) => {
          void startBrowserInstall(appId).then((next) => { if (next) setStatus(next); });
        }}
        onDismiss={() => void dismissInstall().then(setStatus)}
      />
    );
  }

  // Signed out: everything that needs an account is shown but disabled, so the
  // panel still explains what it does instead of hiding it. "Signed out" is a
  // verified answer, not just "no token on disk" — a revoked or expired
  // credential used to leave every control enabled and every action failing.
  const signedOut = !status.signed_in;

  const activeShelf = shelves?.ok && typeof shelves.shelf_id === "number"
    ? (shelves.shelves ?? []).find((s) => s.id === shelves.shelf_id) ?? null
    : null;

  // The pairing code for a sign-in in progress.
  //
  // Shown here, persistently, because the browser that ASKS for it is the same
  // action that closes this panel — a code displayed once, in the panel, would
  // be gone by the time it was needed. It comes from the backend's status so
  // reopening the panel over the browser shows it again.
  // Either a sign-in we just started (code in hand, browser not yet opened) or
  // one already in flight (code recovered from the backend, because this panel
  // was closed by the browser and has since been reopened over it).
  const pairingCode = pendingLogin?.code || status.pending_login_code || "";

  return (
    <>
      {pairingCode ? (
        <PanelSection title="Finish signing in">
          <PanelSectionRow>
            <Focusable
              onFocus={(event: React.FocusEvent<HTMLDivElement>) =>
                event.target.scrollIntoView({ behavior: "smooth", block: "start" })
              }
              style={{ padding: "8px 0" }}
            >
              <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 6 }}>
                Type this code on the Steam sign-in page:
              </div>
              <div
                style={{
                  fontSize: 28,
                  fontWeight: 700,
                  letterSpacing: 6,
                  textAlign: "center",
                  padding: "10px 0",
                  borderRadius: 8,
                  background: theme.surfaceRaised,
                  color: theme.accentText,
                  // Read off this screen and typed on another; a font where
                  // 0/O and 1/I differ matters more here than elsewhere.
                  fontFamily: "monospace",
                }}
              >
                {pairingCode}
              </div>
              <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6 }}>
                It proves this Deck started the sign-in. Nothing is linked to
                your account until it is entered.
              </div>
            </Focusable>
          </PanelSectionRow>

          {pendingLogin && (
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                onClick={() => openSteamSignIn(pendingLogin.url, pendingLogin.session)}
              >
                <IconLabel icon={<FaSteam size={12} />}>
                  Open Steam sign-in
                </IconLabel>
              </ButtonItem>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.7 }}>
              Reopen this panel any time to see the code again.
            </div>
          </PanelSectionRow>
        </PanelSection>
      ) : null}
      <PanelSection title="Shelfcloud">
        <PanelSectionRow>
          {/* Focusable, even though there is nothing here to activate.
              The Quick Access menu scrolls to whatever has focus, so a block
              the controller cannot focus is a block the controller cannot
              scroll to. As plain markup this header was reachable only until
              you scrolled past it: after that the topmost focusable thing was
              the button below it, and pushing up could never go higher — the
              count and the bar stayed off-screen with no way back short of
              closing the panel.

              onFocus scrolls it fully into view rather than just far enough,
              so landing here puts the whole header on screen. */}
          <Focusable
            onFocus={(event: React.FocusEvent<HTMLDivElement>) =>
              event.target.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          >
            <LibrarySummary
              synced={status.tracked}
              // Named only when this device is actually ON a shelf. Shared is
              // spelled null, and there the account-wide total is the right
              // comparison, so the summary keeps saying "of N playable".
              shelfName={activeShelf?.name ?? null}
              shelfGames={activeShelf?.games ?? null}
              available={busy && totalWhenSyncStarted.current !== null
                ? totalWhenSyncStarted.current
                : (owned ?? 0) + status.tracked}
              byProvider={status.by_provider}
              progress={busy ? progress : null}
              progressLabel={message}
            />
          </Focusable>
        </PanelSectionRow>

        {signedOut && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em", marginBottom: 8 }}>
              Sign in with Steam to select cloud games on Shelfcloud. They appear
              here automatically.
            </div>
          </PanelSectionRow>
        )}


        {signedOut && (
          // Signed out this is the topmost control, so it carries the same
          // scroll-to-top duty as the Shelfcloud button does when signed in.
          <div onFocus={scrollPanelToTop}>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={() => void signIn()}>
                Sign in with Steam
              </ButtonItem>
            </PanelSectionRow>
          </div>
        )}

        {/* onFocus, not onClick: focus bubbles, so this fires when the button
            inside receives it. Reaching the topmost control means the header
            above it should be on screen — see scrollPanelToTop. */}
        <div onFocus={scrollPanelToTop}>
          <PanelSectionRow>
            {/* Reachable signed out on purpose: signing in happens there. */}
            <ButtonItem layout="below" onClick={() => openInSteamBrowser(GATEWAY_URL)}>
              {signedOut ? "Open Shelfcloud" : "Select games on Shelfcloud"}
            </ButtonItem>
          </PanelSectionRow>
        </div>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy || signedOut} onClick={() => void sync()}>
            {busy ? "Syncing…" : "Sync now"}
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy || signedOut} onClick={() => void addOwned()}>
            {owned ? `Add all my games (${owned})` : "Add all my games"}
          </ButtonItem>
        </PanelSectionRow>

        {(status.last_error || (message && !busy)) && (
          <PanelSectionRow>
            <div style={{
              fontSize: "0.8em",
              opacity: status.last_error ? 1 : 0.75,
              color: status.last_error ? theme.bad : undefined,
              // Fixed two-line box: progress messages change length constantly,
              // and letting the box grow and shrink makes the panel jump.
              minHeight: "2.4em",
              maxHeight: "2.4em",
              overflow: "hidden",
              lineHeight: "1.2em",
            }}>
              {status.last_error || message}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {/* Settings, Links and About as collapsed sections, so the panel opens
          on the things you actually came for and everything else is one press
          away rather than a scroll away.

          About sits last deliberately: version and server are what you look up
          when something is wrong, not what you reach for daily, so they belong
          furthest from the controls. */}
      <PanelSection>
        <Collapsible title="Settings" icon={<FaCog size={14} />}>
          <PanelSectionRow>
            <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Browser</div>
              <Dropdown
                selectedOption={status.browser?.app_id}
                rgOptions={status.browsers.map((b) => ({ data: b.app_id, label: b.label }))}
                onChange={(option) => { void setSetting("browser", option.data).then(setStatus); }}
              />
            </Focusable>
          </PanelSectionRow>

          <PanelSectionRow>
            <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <div style={{ fontSize: "0.75em", opacity: 0.7 }}>Sync every</div>
              <Dropdown
                disabled={signedOut}
                selectedOption={status.sync_minutes}
                rgOptions={status.sync_choices.map((m) => ({
                  data: m,
                  label: m === 60 ? "hour" : `${m} min`,
                }))}
                onChange={(option) => { void setSetting("sync_minutes", option.data).then(setStatus); }}
              />
            </Focusable>
          </PanelSectionRow>

          {/* Which machine the gateway thinks this is.
              Shown because the website lists devices by name and lets them be
              renamed, and without seeing the name here there is no way to tell
              which row in that list is the Deck in your hands. Read-only: this
              is the one screen with no keyboard worth typing on. */}
          {/* Only offered once a shelf exists. With none there is nothing to
              choose between, and a one-option picker is a control that does
              nothing but invite a press. */}
          {shelves?.ok && (shelves.shelves?.length ?? 0) > 0 && (
            <PanelSectionRow>
              <Focusable style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {/* The device goes in the LABEL rather than on a line of its
                    own. This section is a column of label-above, control-below
                    pairs, and a read-only value has no control to sit under —
                    so as its own row it read as stray text wedged between two
                    dropdowns. Here it is the context the control needs anyway:
                    which machine this setting is about. */}
                <div style={{ fontSize: "0.75em", opacity: 0.7 }}>
                  {shelves.device_name ? `Shelf for ${shelves.device_name}` : "Use shelf"}
                </div>
                <Dropdown
                  disabled={signedOut || busy}
                  // A Dropdown option needs a non-null key, but "no shelf" is
                  // spelled null. SHARED is the stand-in, and it is -1 rather
                  // than 0 because a shelf id comes from a Postgres identity
                  // column: 0 is merely unlikely, a negative id is impossible.
                  // With 0, a shelf that ever had that id would both display
                  // as "Shared games" and, when picked, switch the device to
                  // shared — the opposite of what was chosen.
                  selectedOption={shelves.shelf_id ?? SHARED}
                  rgOptions={[
                    { data: SHARED, label: "Shared games" },
                    ...(shelves.shelves ?? []).map((shelf) => ({
                      data: shelf.id,
                      label: `${shelf.name} (${shelf.games})`,
                    })),
                  ]}
                  onChange={(option) => {
                    const next = option.data === SHARED ? null : Number(option.data);
                    void setShelf(next).then((result) => {
                      if (!result.ok) {
                        toaster.toast({ title: "Shelfcloud", body: result.error || "Could not change shelf",
                                        logo: <NotificationLogo /> });
                        return;
                      }
                      // The library this device should hold just changed, so
                      // reconcile now. Waiting for the next poll would leave
                      // the old tiles in place and read as the setting not
                      // having taken.
                      //
                      // Only doSync() here, with no getShelves() beside it:
                      // finishing a sync bumps `completed`, and the effect
                      // keyed on it re-reads the shelves. Doing both launched
                      // two overlapping reads whose order nothing enforced, so
                      // the older answer could land last and put the picker
                      // back to the shelf just switched away from.
                      void doSync();
                    }).catch((error) => {
                      // Without this the promise rejects unhandled and the
                      // control simply does nothing when pressed — no toast,
                      // no reverted selection, nothing to act on. The backend
                      // returns errors rather than raising, so this is the
                      // last resort: the RPC bridge itself failing.
                      toaster.toast({ title: "Shelfcloud", body: String(error),
                                      logo: <NotificationLogo /> });
                    });
                  }}
                />
              </Focusable>
            </PanelSectionRow>
          )}

          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => {
                void applyOverrides().then((r) => {
                  toaster.toast({
                    title: "Shelfcloud",
                    body: r.ok
                      ? `Gamepad permissions applied to ${r.browser}.`
                      : (r.error || "No browser selected."),
                    logo: <NotificationLogo />,
                  });
                });
              }}
            >
              <IconLabel icon={<FaGamepad size={12} />}>Fix gamepad permissions</IconLabel>
            </ButtonItem>
          </PanelSectionRow>

          {/* A rule and real space before the destructive action.
              Measured on the live panel: this row sat 10px below the "Sync
              every" dropdown where every other button has 20px, because a
              dropdown row is a Focusable wrapping a label and a control rather
              than a plain button, and it contributes different spacing. At
              10px the focus highlight — which is drawn outside the button —
              touched the dropdown above it, so arming this control looked
              cramped and clipped.

              A divider rather than just a bigger margin: this is the one
              control on the panel that destroys something, and it should read
              as separate from the settings it sits under. */}
          <div style={{
            height: 1,
            background: theme.border,
            margin: "16px 16px 4px",
          }} />

          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || signedOut} onClick={() => void removeAll()}>
              {/* ButtonItem has no destructive variant, so the label carries the
                  warning colour. Colour and weight are the only things that
                  differ from any other label — same flex row, no vertical
                  padding, inherited line-height.

                  The armed text is kept short and nowrap because it must fit on
                  one line. Measured on the live panel: the button gives 189px
                  to the label, and "Confirm — remove all tiles" needs 200px
                  bold. It wrapped, which grew the row from 40px to 60px, so
                  arming the button made the whole panel shift under the user's
                  thumb. "Confirm — remove all" is 164px bold and fits.

                  nowrap is the guarantee rather than the fix: it keeps the row
                  at 40px even if a future label — or a translation — runs long.
                  Two earlier attempts here added padding and line-height to
                  "give the text room", which only made the row taller still;
                  nothing was ever being clipped. */}
              <IconLabel
                icon={<FaTrashAlt size={12} color={confirmClear ? "#ff4444" : theme.bad} />}
              >
                <span style={{
                  color: confirmClear ? "#ff4444" : theme.bad,
                  fontWeight: confirmClear ? 700 : 500,
                  whiteSpace: "nowrap",
                }}>
                  {confirmClear ? "Confirm — remove all" : "Remove all games"}
                </span>
              </IconLabel>
            </ButtonItem>
          </PanelSectionRow>

          <PanelSectionRow>
            <ButtonItem layout="below" disabled={signedOut} onClick={() => void signOut()}>
              <IconLabel icon={<FaSignOutAlt size={12} />}>
                <span style={{
                  color: confirmSignOut ? "#ff4444" : undefined,
                  fontWeight: confirmSignOut ? 700 : 500,
                }}>
                  {confirmSignOut ? "Confirm — sign out" : "Sign out"}
                </span>
              </IconLabel>
            </ButtonItem>
          </PanelSectionRow>
        </Collapsible>

        <Collapsible title="Links" icon={<FaLink size={14} />}>
          <PanelSectionRow>
            {/* Reachable signed out: the documentation is how someone works out
                what to do about being signed out. */}
            {/* The guide lives on the website, not in this panel.
                A guide with pictures does not fit in a Quick Access panel, and
                one served from the gateway can be corrected without shipping a
                plugin release to every device. */}
            <ButtonItem
              layout="below"
              onClick={() => openInSteamBrowser(`${GATEWAY_URL}/guide`)}
            >
              <IconLabel icon={<FaQuestionCircle size={14} />}>
                Help &amp; documentation
              </IconLabel>
            </ButtonItem>
          </PanelSectionRow>

          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => openInSteamBrowser(GATEWAY_URL)}>
              <IconLabel icon={<FaGlobe size={14} />}>Shelfcloud website</IconLabel>
            </ButtonItem>
          </PanelSectionRow>

          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => openInSteamBrowser(GITHUB_URL)}>
              <IconLabel icon={<FaGithub size={14} />}>GitHub repository</IconLabel>
            </ButtonItem>
          </PanelSectionRow>
        </Collapsible>

        <Collapsible title="About" icon={<FaInfoCircle size={14} />}>
          <PanelSectionRow>
            <Field label="Version" bottomSeparator="none">
              {status.version || "unknown"}
            </Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Games synced" bottomSeparator="none">{status.tracked}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Signed in" bottomSeparator="none">
              <span style={{ color: signedOut ? theme.bad : theme.good }}>
                {signedOut ? "No" : "Yes"}
              </span>
            </Field>
          </PanelSectionRow>
          {/* The name the website lists this machine under, so the row you
              rename there can be matched to the Deck in your hands. A fact
              rather than a setting, so it belongs here with the others —
              Field is the row Decky draws them with, already used above. */}
          {shelves?.ok && shelves.device_name && (
            <PanelSectionRow>
              <Field label="This device" bottomSeparator="none">
                {shelves.device_name}
              </Field>
            </PanelSectionRow>
          )}
        </Collapsible>
      </PanelSection>

      {/* Decky's tab bar butts straight against the end of the panel. A focused
          ButtonItem draws its ring outside its own box, so the last control's
          ring was being cut off — 16px was enough for the button but not for
          the ring around it. */}
      <div style={{ height: 28 }} />
    </>
  );
}

/** What to say when games appear on their own.
 *
 *  Names the game when there is one, because "1 new game" makes you go and
 *  look. Beyond a few, names would overflow the toast, so it counts instead.
 */
function newGamesToast(names: string[]) {
  if (names.length === 1) {
    return { title: "Added to your library", body: names[0],
             logo: <NotificationLogo /> };
  }
  if (names.length <= 3) {
    return { title: `${names.length} games added`, body: names.join(", "),
             logo: <NotificationLogo /> };
  }
  return {
    title: `${names.length} games added`,
    body: `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`,
    logo: <NotificationLogo />,
  };
}

export default definePlugin(() => {
  // Registered here, NOT in the panel component: Decky keeps this alive for the
  // whole Steam session, whereas the panel only exists while it is open. With
  // the listener in the component, "background" sync only ran while the user
  // was watching it — which is not background at all.
  const onLibraryChanged = () => {
    void runSync()
      .then(({ result, owned }) => {
        // Only the run that actually did the work notifies, and only when
        // something arrived. A sync runs every minute and almost always finds
        // nothing; notifying on completion would be a toast every minute, and
        // notifying from every waiter would be one toast per poll tick that
        // happened to land during a long sync.
        if (owned && result.added > 0) toaster.toast(newGamesToast(result.addedNames));
      })
      .catch((error) => {
        console.error("[shelfcloud] background sync failed", error);
      });
  };
  addEventListener(SYNC_EVENT, onLibraryChanged);



  return {
    name: "Shelfcloud",
    titleView: (
      <div className={staticClasses.Title}
           style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Logo size={22} />
        <span>Shelfcloud</span>
      </div>
    ),
    content: <Content />,
    // Monochrome and em-sized, so it matches its neighbours in Decky's plugin
    // list: they are react-icons that take their colour from the row and turn
    // white when it is selected. A coloured, pixel-sized mark stood out as
    // both larger and out of place.
    icon: <LogoMono />,
    onDismount() {
      removeEventListener(SYNC_EVENT, onLibraryChanged);
    },
  };
});
