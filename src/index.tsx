// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import { addEventListener, definePlugin, removeEventListener, routerHook, toaster } from "@decky/api";
import {
  ButtonItem,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { FaSteam } from "react-icons/fa";

import {
  forgetShortcut,
  addOwnedGames,
  clearLibrary,
  dismissInstall,
  dropFromLibrary,
  restoreToLibrary,
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
import { syncLibrary, untiledFromRun } from "./lib/sync";
import { changeSummary, syncSummary } from "./lib/summary";
import { LibrarySummary } from "./components/LibrarySummary";
import { Logo, LogoMono, NotificationLogo } from "./components/Logo";
import { CollapsibleGroup, IconLabel } from "./components/Collapsible";
import { NoBrowser } from "./components/NoBrowser";
import { SettingsSection } from "./components/SettingsSection";
import { LinksSection } from "./components/LinksSection";
import { AboutSection } from "./components/AboutSection";
import { cancelJob, cancelRequested, finishJob, getJob, runJob, setAbandonHandler, setJob, subscribeJob } from "./lib/job";
import { theme } from "./lib/theme";
import { isSafeExternalUrl } from "./lib/url";
import { badgeDataNow, invalidateBadgeData, refreshBadgeData } from "./lib/badgeData";
import { startFocusBadge, stopFocusBadge } from "./lib/focusBadge";
import { patchLibraryApp } from "./lib/patchLibraryApp";
import type { DesiredGame, InstallState, LibraryResponse, Status, SyncResult } from "./lib/types";

const SYNC_EVENT = "shelfcloud/library";
const LOGIN_POLL_MS = 2500;
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const GATEWAY_URL = "https://shelfcloud.cc";
const GITHUB_URL = "https://github.com/bschelst/shelfcloud-decky";

/** How often to re-read the library count during a sync - it climbs, but is not a call per tile. */
const COUNT_POLL_MS = 1500;

/** A loud, unmistakable "a sync is running" banner. Greyed buttons alone were
 *  too quiet. The spinner is an SVG that animates itself (SMIL): a CSS
 *  @keyframes needs a stylesheet in the panel's own document, which Game Mode
 *  does not give us, so it never span. The text is one fixed line that never
 *  wraps, so the panel cannot jump between one and two rows as it changes. */
function SyncBanner() {
  // Deliberately NOT the per-tile scan line ("Checking X (0/0)"). That read as
  // stuck and told the user nothing useful; one calm word is what they asked for.
  const text = "Syncing…";
  // When a sync starts the banner mounts; scroll the panel up so it is in view.
  // Pressing "Sync now" lower down otherwise left the banner off-screen.
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollAncestorToTop(ref.current); }, []);
  return (
    <PanelSectionRow>
      <div ref={ref} style={{
        display: "flex", alignItems: "center", gap: 10, width: "100%",
        height: 36, padding: "0 12px", borderRadius: 8, boxSizing: "border-box",
        background: theme.tint(theme.accent, 0.18),
        border: `1px solid ${theme.tint(theme.accent, 0.55)}`,
        color: theme.accentText, fontWeight: 700, overflow: "hidden",
      }}>
        <svg width="16" height="16" viewBox="0 0 24 24" style={{ flex: "none" }} aria-hidden="true">
          <circle cx="12" cy="12" r="9" fill="none"
                  stroke={theme.tint(theme.accentText, 0.25)} strokeWidth="3" />
          <path d="M12 3 a 9 9 0 0 1 0 18" fill="none"
                stroke={theme.accentText} strokeWidth="3" strokeLinecap="round">
            <animateTransform attributeName="transform" attributeType="XML"
              type="rotate" from="0 12 12" to="360 12 12" dur="0.8s"
              repeatCount="indefinite" />
          </path>
        </svg>
        <span style={{
          flex: 1, minWidth: 0, whiteSpace: "nowrap",
          overflow: "hidden", textOverflow: "ellipsis",
        }}>{text}</span>
      </div>
    </PanelSectionRow>
  );
}

/** No install started. Mirrors IDLE_INSTALL in main.py so the panel reads the fields even on an older backend. */
const IDLE_INSTALL: InstallState = {
  running: false, app_id: null, label: null, message: "", error: null, done: false,
};

/** Start a browser install and show it right away. The backend returns once the
 *  download starts, so we refresh one time to pick up `running: true` and start the poll. */
async function startBrowserInstall(appId: string): Promise<Status | null> {
  const started = await installBrowser(appId);
  if (!started.ok) {
    console.error("[shelfcloud] could not start install", started.error);
  }
  return getStatus().catch(() => null);
}

/** Scroll the panel back to the very top. The Quick Access menu only scrolls to
 *  what has focus, and the header cannot take focus, so once it went off the top
 *  pushing up could not bring it back. When focus reaches the top control,
 *  scroll its container all the way up so the header shows again. */
function scrollAncestorToTop(start: HTMLElement | null): void {
  let node: HTMLElement | null = start;
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

function scrollPanelToTop(event: React.FocusEvent<HTMLDivElement>): void {
  scrollAncestorToTop(event.currentTarget);
}

/** Open a URL in Steam's built-in browser, the only one Game Mode has. The cast
 *  works around a name clash: `SteamClient.URL` resolves to the DOM's global
 *  `URL`, so ExecuteSteamURL is invisible to TypeScript but is there at runtime. */
type SteamUrlApi = { ExecuteSteamURL(url: string): void };

function openInSteamBrowser(url: string): void {
  if (!isSafeExternalUrl(url)) {
    console.error("[shelfcloud] refusing to open an unsafe URL", url);
    return;
  }
  try {
    (SteamClient.URL as unknown as SteamUrlApi).ExecuteSteamURL(`steam://openurl/${url}`);
    // Retry: on the first call the browser window may not be ready and lands
    // on a blank placeholder.
    setTimeout(() => {
      try {
        (SteamClient.URL as unknown as SteamUrlApi).ExecuteSteamURL(`steam://openurl/${url}`);
      } catch { /* already navigated */ }
    }, 1000);
  } catch (error) {
    console.error("[shelfcloud] could not open browser", error);
    Navigation.NavigateToExternalWeb(url);
  }
}

/** The sync running now, if any. Module scope so the panel and the background
 *  poll can share it. */
let syncInFlight: Promise<SyncOutcome> | null = null;

/** `total` counts every library entry, skipped ones included, so "N games — X added (Y skipped)" adds up. */
type SyncOutcome = { result: SyncResult; total: number; skipped: number };


/** Run a full reconcile, but never two at once. Two overlapping runs both read
 *  the tracked list before either records anything, so both make shortcuts for
 *  the same games and the user gets double tiles. A caller mid-sync gets the
 *  running one, so "Sync now" still reports a real outcome. */
async function runSync(
  onProgress?: (message: string) => void,
): Promise<SyncOutcome & { owned: boolean }> {
  const running = syncInFlight;
  if (running) {
    // Wait for the run in progress and report its outcome, but mark that this
    // caller did not do it. Without `owned` every waiter would fire its own
    // toast - six poll ticks in one long sync means six copies
    return { ...(await running), owned: false };
  }

  const started = performSync(onProgress);
  syncInFlight = started;
  try {
    return { ...(await started), owned: true };
  } finally {
    // Only if it is still ours. Cancel can release the in-flight promise so the
    // next sync starts fresh, and that next run must not have its own promise
    // cleared when this abandoned one finally settles.
    if (syncInFlight === started) syncInFlight = null;
  }
}

/** Is this entry complete enough to build a Steam shortcut from? A half-formed
 *  entry makes a broken tile with no error, so we do the controle before SteamClient. */
function isUsableGame(game: unknown): game is DesiredGame {
  const g = game as DesiredGame;
  return !!g && typeof g === "object"
    && typeof g.game_id === "number"
    && typeof g.name === "string" && g.name.trim() !== ""
    && typeof g.provider === "string" && g.provider !== ""
    && typeof g.exe === "string" && g.exe !== ""
    && typeof g.options === "string" && g.options !== "";
}

/** The last library the backend sent, with its version. Kept so an unchanged one
 *  need not be sent again: 1.6 MB on a large account, sent every sync just to say nothing changed. */
let lastLibrary: { version: string; body: LibraryResponse } | null = null;

async function performSync(onProgress?: (message: string) => void): Promise<SyncOutcome> {
  let library = await getDesiredLibrary(lastLibrary?.version);
  if (library.ok && library.unchanged) {
    if (lastLibrary) {
      library = lastLibrary.body;
    } else {
      // We claimed a version we do not have. Ask opnieuw without one instead of
      // syncing against nothing.
      library = await getDesiredLibrary();
    }
  }
  if (!library.ok) {
    throw new Error(library.error || "Could not read your library");
  }
  if (library.version && Array.isArray(library.desired)) {
    lastLibrary = { version: library.version, body: library };
  }

  // never hand SteamClient something malformed, whatever the gateway said.
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
    shouldStop: cancelRequested,
  });
  await syncFinished(result.added, result.removed, result.updated);
  // Two places drop entries: the backend (library.skipped) and isUsableGame.
  // Both are already gone from `desired`, so the real size is the survivors
  // plus both rejected counts.
  const skipped = (library.skipped ?? 0) + unusable;
  return {
    result,
    total: desired.length + skipped,
    skipped,
  };
}

/** Reconcile the Steam library, reporting progress into the job store. At module scope so it keeps going while the panel is closed. */
async function doSync(quiet = false): Promise<boolean> {
  setJob({ busy: true });
  try {
    const { result, total, skipped } = await runSync((line) => {
      // The sync emits "Adding X (12/124)"; reuse it instead of a second callback.
      const match = line.match(/\((\d+)\/(\d+)\)\s*$/);
      const done = match ? Number(match[1]) : 0;
      const all = match ? Number(match[2]) : 0;
      // Keep the last fraction on lines with no counter, so the bar does not jump back to indeterminate.
      setJob({ message: line, progress: match && all > 0 ? done / all : getJob().progress });
    });
    finishJob(syncSummary(total, result, skipped));
    if (!quiet) toaster.toast({ title: "Shelfcloud", body: changeSummary(result),
                               logo: <NotificationLogo /> });
    // Reported back so "add all my games" can tell a stopped run from a finished one and clean up games it never reached.
    return Boolean(result.cancelled);
  } catch (error) {
    finishJob(String(error));
    if (!quiet) toaster.toast({ title: "Sync failed", body: String(error),
                               logo: <NotificationLogo /> });
    // A failure is not a cancel: the library is left as is so the next sync can finish the job.
    return false;
  }
}

/** The "add all my games" run in progress, if any. Single-flight: a panel closed
 *  and reopened mid-run lost its busy flag and offered the button again, so a second press now joins the run. */
let addOwnedInFlight: Promise<void> | null = null;

/** Every game id in the account's library now, or null if we could not read it.
 *  Null, not an empty set: the caller subtracts this from a later reading, and an
 *  empty set would make every game look newly added and removable. */
async function libraryGameIds(): Promise<Set<number> | null> {
  try {
    const library = await getDesiredLibrary();
    if (!library.ok || !Array.isArray(library.desired)) return null;
    // Filter rows before reading them: one null entry throws, and the catch below
    // turns that into "could not read the library", quietly disabling the cleanup.
    return new Set(library.desired
      .filter((g) => g && typeof g === "object")
      .map((g) => Number(g.game_id))
      .filter((id) => Number.isInteger(id)));
  } catch {
    return null;
  }
}

/** After a cancelled bulk add: take back the games it added but never tiled, or
 *  the next sync would just make them all again. Three conditions, all needed,
 *  because this removes things: added by this run (not in the library before),
 *  has no tile (never reached), and not a web page (exempt from cancel). */
async function forgetUntiled(before: Set<number> | null): Promise<void> {
  if (!before) return;          // could not read the library; change nothing
  try {
    const library = await getDesiredLibrary();
    if (!library.ok || !Array.isArray(library.desired)) return;
    const tracked = (library.tracked && typeof library.tracked === "object")
      ? library.tracked : {};

    const drop = untiledFromRun(before, library.desired, tracked);
    if (!drop.length) return;
    setJob({ message: `Stopped. Putting back ${drop.length} games…` });
    const result = await dropFromLibrary(drop);
    finishJob(result?.ok
      ? `Stopped — kept what was added, put back ${drop.length}.`
      : "Stopped — some games could not be put back.");
  } catch (error) {
    console.error("[shelfcloud] could not tidy a cancelled add", error);
  }
}

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

      // What was in the library before the bulk add. Taken first so a cancel can
      // tell this run's games from the user's own choices - only ours may go.
      const beforeAdd = await libraryGameIds();

      setJob({ message: `You own ${steam.owned} Steam games. Adding the cloud ones…` });
      const result = await addOwnedGames();
      if (!result.ok) {
        finishJob(result.error || "Could not add games");
        return;
      }

      setJob({ message: `Added ${result.added} games. Syncing to your library…` });
      const stopped = await doSync(true);
      if (stopped) await forgetUntiled(beforeAdd);
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

/** Empty the library and let a sync delete every tile we made. Wrapped in runJob
 *  so every exit leaves the panel idle - it used to miss one and leave the buttons greyed out. */
async function doRemoveAll(): Promise<void> {
  await runJob(async () => {
    const result = await clearLibrary();
    if (!result.ok) return result.error || "Could not clear your library";
    setJob({ message: `Removed ${result.removed} games. Removing all tiles…` });
    // Remove every tracked tile without asking the gateway. It is already empty,
    // but reading it (doSync) would re-add the website tile first. An empty
    // desired list means "remove everything tracked".
    const library = await getDesiredLibrary();
    const tracked = (library.ok && library.tracked && typeof library.tracked === "object")
      ? library.tracked : {};
    const syncResult = await syncLibrary([], tracked, {
      record: recordShortcut,
      forget: forgetShortcut,
      // Cancellable. Without this the button said "Stopping…" but kept deleting,
      // because the stop was only checked in the add loop and this run adds none.
      shouldStop: cancelRequested,
    });
    await syncFinished(syncResult.added, syncResult.removed, syncResult.updated);

    if (syncResult.cancelled) {
      // Stopping the deletions is not enough: the library was emptied
      // server-side first, so every saved tile is no longer wanted and the next
      // sync would take it. Put the survivors back, only those. Read them back,
      // do not reuse `tracked` - that copy would restore the already-deleted
      // tiles, the opposite of what we want.
      const after = await getDesiredLibrary().catch(() => null);
      // tracked is keyed "gameId:provider" (one key per tile), so Number(key)
      // on the whole key is NaN and every surviving tile was dropped - restore
      // put back almost nothing and the library count collapsed to a handful.
      // Split off the game id, and dedupe: a game on two services has two keys.
      const survivors = [...new Set(
        Object.keys(
          (after?.ok && after.tracked && typeof after.tracked === "object")
            ? after.tracked : {},
        ).map((key) => Number(key.split(":")[0])).filter((id) => Number.isInteger(id)),
      )];

      if (!survivors.length) return `Stopped — removed ${syncResult.removed}.`;
      const kept = await restoreToLibrary(survivors).catch(() => null);
      return kept?.ok
        ? `Stopped — removed ${syncResult.removed}, kept ${survivors.length}.`
        : `Stopped after ${syncResult.removed}, but the rest could not be kept.`;
    }

    return syncResult.removed === 1
      ? "Removed 1 tile."
      : `Removed ${syncResult.removed} tiles.`;
  });
}

/** The last status the panel saw, kept outside React. Closing Quick Access
 *  unmounts the panel, so state is lost. Starting from null made every open show
 *  "Loading…" while it re-fetched. None of this is worth blocking on, so show it
 *  at once and correct it when the answer comes. */
let lastStatus: Status | null = null;

function Content() {
  const [status, setStatusState] = useState<Status | null>(lastStatus);
  // Every write goes through here so the cache cannot drift from the view.
  const setStatus = useCallback(
    (next: Status | ((prev: Status | null) => Status | null)) => {
      setStatusState((prev) => {
        const value = typeof next === "function" ? next(prev) : next;
        lastStatus = value;
        return value;
      });
    }, []);
  const [owned, setOwned] = useState<number | null>(null);
  // Which shelf this device shows, and what it could show. From the gateway, not
  // settings: the shelf can change on the website or another device.
  const [shelves, setShelves] = useState<Awaited<ReturnType<typeof getShelves>> | null>(null);
  // Two-press confirm: this deletes every tile the plugin made.
  const [confirmClear, setConfirmClear] = useState(false);
  // Cancel was pressed for the run in flight. Local to the panel, not the job
  // store: it is only about this button's label. The run stops between tiles, so
  // a button still saying "Cancel" invites a second press.
  const [stopping, setStopping] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const loginTimer = useRef<number | null>(null);
  // A sign-in started but not yet handed to the browser. The gap lets the pairing
  // code be read before Steam's browser takes the screen and closes this panel.
  const [pendingLogin, setPendingLogin] =
    useState<{ url: string; code: string; session: string } | null>(null);
  // "of N playable", held still for the duration of a sync — see below.
  const totalWhenSyncStarted = useRef<number | null>(null);

  // Whatever is running now, even if it started before this panel opened. This
  // is what makes a reopened panel show live progress, not an idle one.
  const { busy, message, progress, completed } =
    useSyncExternalStore(subscribeJob, getJob);

  // Set when the backend cannot be reached, so the panel can say so.
  const [unreachable, setUnreachable] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    // Each call stands alone, and a failure is reported, not thrown. A bare
    // `setStatus(await getStatus())` left `status` null when the socket dropped,
    // so the panel sat on "Loading…" and looked broken.
    try {
      const next = await getStatus();
      if (next) {
        setStatus(next);
        setUnreachable(null);
      }
    } catch (error) {
      setUnreachable(String((error as Error)?.message || error));
    }

    // How many owned games could still be added, so the button can show it. Its
    // own try: a count we cannot read must not cost the settings too.
    try {
      const preview = await previewOwned();
      setOwned(preview.ok ? (preview.not_added ?? 0) : null);
    } catch {
      setOwned(null);
    }
  }, []);

  // On mount, and again when a job finishes, so the counts follow work done with the panel closed.
  useEffect(() => { void refresh(); }, [refresh, completed]);

  // Put Cancel back to its normal label once its run has really stopped, so the next one does not open saying "Stopping…".
  useEffect(() => { if (!busy) setStopping(false); }, [busy]);

  // Read with the status, and again after a sync: a sync is when the library changes, so a shelf's count can be stale.
  useEffect(() => {
    void getShelves().then(setShelves).catch(() => setShelves(null));
  }, [completed]);

  // While a sync runs, keep the headline count honest. It comes from getStatus(),
  // refreshed only when a job finished, so a long run sat at 0 while the bar moved.
  // Polled, not counted locally: the tracked list is the truth.
  useEffect(() => {
    if (!busy) {
      totalWhenSyncStarted.current = null;
      return undefined;
    }
    // Freeze the denominator for the run. It is owned-not-yet-added plus
    // already-tracked; only the second half is refreshed here. Left alone it
    // would climb with the count, and adding games doesnt change how many are
    // playable.
    if (totalWhenSyncStarted.current === null) {
      totalWhenSyncStarted.current = (owned ?? 0) + (status?.tracked ?? 0);
    }
    const timer = window.setInterval(() => {
      void getStatus().then(setStatus).catch(() => {});
    }, COUNT_POLL_MS);
    return () => window.clearInterval(timer);
    // Depends only on `busy` on purpose. Adding `owned` or `status` would re-run
    // this every poll, and the frozen total must be the value from when the sync
    // began, not a tick later.
  }, [busy]);

  // While a browser is downloading, keep reading its progress. Separate from the
  // sync poll: an install is not a "job" - it lives in the backend so it survives
  // the panel closing. When it finishes the same poll finds the new browser.
  const installing = status?.install?.running ?? false;
  useEffect(() => {
    if (!installing) return undefined;
    const timer = window.setInterval(() => {
      void getStatus().then(setStatus).catch(() => {});
    }, COUNT_POLL_MS);
    return () => window.clearInterval(timer);
  }, [installing]);

  // Cancel any login poll when the panel unmounts, so a forgotten timer cannot
  // keep hitting the gateway for five minutes.
  useEffect(() => () => {
    if (loginTimer.current) window.clearInterval(loginTimer.current);
  }, []);

  const sync = useCallback((quiet = false) => doSync(quiet), []);
  const addOwned = useCallback(() => doAddOwned(), []);

  // The background sync listener is NOT registered here on purpose: this
  // component only mounts while the panel is open, so it would run only while
  // the user watches. It lives in definePlugin() instead, alive all session.

  const signIn = useCallback(async () => {
    setPendingLogin(null);
    setStatus((prev) => prev ? { ...prev, pending_login_code: "" } : prev);
    setJob({ busy: true, message: "Opening Steam sign-in…" });
    const start = await startLogin();
    if (!start.ok || !start.login_url || !start.session_token) {
      finishJob(start.error || "Could not start sign-in");
      return;
    }

    // The browser is NOT opened here any more. Opening Steam's browser closes
    // the Quick Access Menu, where the pairing code lives, so firing it right
    // away made the panel vanish before the code was ever seen. The code is
    // shown first, with a button to continue; the user reads it and presses
    // Open when ready.
    setPendingLogin({ url: start.login_url, code: start.confirm_code || "",
                      session: start.session_token });
    setJob({ busy: false, message: "" });
  }, [refresh, sync]);

  const openSteamSignIn = useCallback((url: string, session: string) => {
    openInSteamBrowser(url);

    // The backend waits for the sign-in and stores the token, so it finishes even
    // though opening the browser closes this panel. This only watches, and losing the watcher no longer loses the sign-in.
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

  /** Empty the library and let sync delete every tile we made. */
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
    // Nothing read yet. Two very different reasons, and the panel used to show
    // the same word for both, so a dead backend looked like one about to
    // answer.
    return (
      <PanelSection title="Shelfcloud">
        {unreachable ? (
          <>
            <PanelSectionRow>
              <div style={{ fontSize: "0.85em", lineHeight: 1.45 }}>
                Cannot reach the plugin&rsquo;s backend.
                <div style={{ opacity: 0.7, marginTop: 4 }}>{unreachable}</div>
              </div>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" onClick={() => void refresh()}>
                Try again
              </ButtonItem>
            </PanelSectionRow>
          </>
        ) : (
          <PanelSectionRow>Loading…</PanelSectionRow>
        )}
      </PanelSection>
    );
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
  // panel still explains itself. "Signed out" is a checked answer, not just "no
  // token on disk" - a revoked one used to leave every control on but failing.
  const signedOut = !status.signed_in;

  const activeShelf = shelves?.ok && typeof shelves.shelf_id === "number"
    ? (shelves.shelves ?? []).find((s) => s.id === shelves.shelf_id) ?? null
    : null;

  // The pairing code for a sign-in in progress. Shown here and kept, because the
  // browser that asks for it is what closes this panel. From the backend's status, so reopening the panel shows it again.
  const pairingCode = status.signed_in ? "" : (pendingLogin?.code || status.pending_login_code || "");

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

          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={() => {
                if (pendingLogin) {
                  openSteamSignIn(pendingLogin.url, pendingLogin.session);
                } else {
                  // Falls back to /my, not the root: this button reopens the
                  // sign-in page, which lives with the library. The root would
                  // show the home page.
                  const url = (status as unknown as Record<string, unknown>).pending_login_url as string || `${GATEWAY_URL}/my`;
                  openInSteamBrowser(url);
                }
              }}
            >
              <IconLabel icon={<FaSteam size={12} />}>
                {pendingLogin ? "Open Steam sign-in" : "Reopen sign-in page"}
              </IconLabel>
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Same marginTop as the note above the button, so both footnotes
                sit the same distance from what they explain. */}
            <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6 }}>
              Reopen this panel any time to see the code again.
            </div>
          </PanelSectionRow>
        </PanelSection>
      ) : null}
      <PanelSection title="Shelfcloud">
        {busy && <SyncBanner />}
        <PanelSectionRow>
          {/* Focusable, even with nothing to activate. The Quick Access menu
              scrolls to whatever has focus, so a block that cannot take focus
              cannot be scrolled to; as plain markup this header could not be
              reached again after you scrolled past it. onFocus scrolls it
              fully into view so the whole header shows. */}
          <Focusable
            onFocus={(event: React.FocusEvent<HTMLDivElement>) =>
              event.target.scrollIntoView({ behavior: "smooth", block: "start" })
            }
          >
            <LibrarySummary
              synced={status.tracked}
              // Named only when this device is on a shelf. Shared is null, and
              // there the account-wide total is the right compare.
              shelfName={activeShelf?.name ?? null}
              shelfGames={activeShelf?.games ?? null}
              available={busy && totalWhenSyncStarted.current !== null
                ? totalWhenSyncStarted.current
                : (owned ?? 0) + status.tracked}
              byProvider={status.by_provider}
              progress={busy ? progress : null}
              // The banner above says "Syncing…" now; leaving the per-tile line
              // here too showed "(0/0)" and jumped the chips in and out. Keep the
              // chips steady during a run.
              progressLabel={busy ? "" : message}
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
          // Signed out, this is the topmost control, so it does the same
          // scroll-to-top job as the Shelfcloud button when signed in.
          <div onFocus={scrollPanelToTop}>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={() => void signIn()}>
                Sign in with Steam
              </ButtonItem>
            </PanelSectionRow>
          </div>
        )}

        {/* onFocus, not onClick: focus bubbles, so this fires when the button
            inside gets it. Reaching the top control means the header above
            should be on screen - see scrollPanelToTop. */}
        <div onFocus={scrollPanelToTop}>
          <PanelSectionRow>
            {/* Straight to the library, not the site root. The root is the
                home page now, so sending someone there to pick games would
                land them on the pitch with another click to make. /my is the
                library, and shows the sign-in first if needed. Reachable
                signed out on purpose: signing in happens there. */}
            <ButtonItem layout="below" onClick={() => openInSteamBrowser(`${GATEWAY_URL}/my`)}>
              {signedOut ? "Open Shelfcloud" : "Select games on Shelfcloud"}
            </ButtonItem>
          </PanelSectionRow>
        </div>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy || signedOut} onClick={() => void addOwned()}>
            {owned ? `Add all my games (${owned})` : "Add all my games"}
          </ButtonItem>
        </PanelSectionRow>

        {/* While something runs this row becomes the way out of it. "Add all
            my games" walks a whole Steam library, many minutes on a large
            account, and before this the only way to stop was to kill Steam.
            The button is only swapped while busy, so there is never a Cancel
            with nothing to cancel. */}
        <PanelSectionRow>
          {busy ? (
            <ButtonItem
              layout="below"
              disabled={stopping}
              onClick={() => { setStopping(true); cancelJob(); }}
            >
              {stopping ? "Stopping…" : "Cancel"}
            </ButtonItem>
          ) : (
            <ButtonItem layout="below" disabled={signedOut} onClick={() => void sync()}>
              Sync now
            </ButtonItem>
          )}
        </PanelSectionRow>

        {(status.last_error || (message && !busy)) && (
          <PanelSectionRow>
            <div style={{
              fontSize: "0.8em",
              opacity: status.last_error ? 1 : 0.75,
              color: status.last_error ? theme.bad : undefined,
              // Fixed two-line box: progress messages change length all the
              // time, and a box that grows and shrinks makes the panel jump.
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

      {/* One at a time. Settings, Links and About are each long enough that
          two open at once push everything below them off a 7-inch screen. */}
      <PanelSection>
        <CollapsibleGroup>
          <SettingsSection
            status={status}
            setStatus={setStatus}
            signedOut={signedOut}
            busy={busy}
            shelves={shelves}
            confirmClear={confirmClear}
            confirmSignOut={confirmSignOut}
            onRemoveAll={() => void removeAll()}
            onSignOut={() => void signOut()}
            onApplyOverrides={() => {
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
            onSetSetting={setSetting}
            // Turning "a tile per service" ON must bring the games in, not just
            // re-shape the ones there. A library with 8 of Luna's 119 games
            // would give 8 tiles and look broken. doAddOwned does the whole
            // thing: reads the library, adds everything playable and syncs.
            onTilePerServiceEnabled={() => void doAddOwned()}
            onSetShelf={setShelf}
            onShelfChanged={() => void doSync()}
            toaster={toaster}
          />
          <LinksSection
            gatewayUrl={GATEWAY_URL}
            githubUrl={GITHUB_URL}
            openInBrowser={openInSteamBrowser}
          />
          <AboutSection
            version={status.version || "unknown"}
            tracked={status.tracked}
            signedOut={signedOut}
            deviceName={shelves?.ok ? shelves.device_name : null}
          />
        </CollapsibleGroup>
      </PanelSection>

      {/* Decky's tab bar sits right against the end of the panel. A focused
          ButtonItem draws its ring outside its box, so the last control's ring
          was cut off - 16px was enough for the button but not the ring. */}
      <div style={{ height: 28 }} />
    </>
  );
}

/** What to say when games appear on their own. Names the game when there is
 *  one; beyond a few, names would overflow the toast, so it counts instead. */
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
  // Registered here, NOT in the panel: Decky keeps this alive for the whole Steam
  // session, but the panel only exists while open. In the component, "background" sync ran only while the user watched.
  const onLibraryChanged = () => {
    // Mark the panel busy for the whole run, exactly like a manual sync, so
    // "Sync now" and "Add all my games" grey out and a "Syncing…" line shows
    // while the auto-sync works. Without this the auto-sync was invisible, and
    // pressing Add-all over the top of it joined the already-running sync - which
    // had read the library BEFORE the add, so the newly added games got no tiles
    // or collections. Disabling the buttons while it runs is what prevents that.
    // One clear line for the whole run, not the per-tile "Checking X (0/0)"
    // scan text -- that read as a stuck error, and because the background sync
    // ended on finishJob() with no message, the last such line was never
    // cleared and sat there forever. "Syncing…" stays up while busy, then is
    // cleared on finish. No onProgress, so nothing overwrites it.
    setJob({ busy: true, message: "Syncing…", progress: null });
    void runSync()
      .then(({ result, owned }) => {
        finishJob("");
        // Only the run that did the work notifies, and only when something arrived.
        if (owned && result.added > 0) toaster.toast(newGamesToast(result.addedNames));
        // Tiles were made or removed, so the badge's app-id map is stale. Cheaper than a shorter cache life, and exact.
        invalidateBadgeData();
      })
      .catch((error) => {
        finishJob("");
        console.error("[shelfcloud] background sync failed", error);
      });
  };
  // Cancel gives up after ten seconds and resets the panel while the run is still
  // going. Let go of it here, or the next sync waits on that dead promise.
  setAbandonHandler(() => { syncInFlight = null; });

  addEventListener(SYNC_EVENT, onLibraryChanged);

  // The provider badge on Steam's own game page. Patched here, not in the panel:
  // the panel exists only while open, and the badge must be there on every game
  // page.
  const libraryPatch = patchLibraryApp();

  // And the same mark on the highlighted card in the home carousel and grid. Not
  // a React patch: those cards are recycled by a virtual list, nothing stable to hang a child on - see focusBadge.
  refreshBadgeData();
  startFocusBadge(() => {
    // Kept fresh from here, not inside the observer, which runs while the highlight moves and must not wait.
    refreshBadgeData();
    return badgeDataNow();
  });



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
    // Monochrome and em-sized so it matches its neighbours in Decky's plugin
    // list: they take their colour from the row and turn white when selected.
    icon: <LogoMono />,
    onDismount() {
      removeEventListener(SYNC_EVENT, onLibraryChanged);
      // Removed on purpose. A patch left behind outlives the unloaded plugin and keeps rendering a dead component.
      try {
        routerHook.removePatch("/library/app/:appid", libraryPatch);
      } catch (error) {
        console.error("[shelfcloud] could not remove the library patch", error);
      }
      // Disconnects the observer AND takes the badge off whatever card it is on -
      // a node left in another window's document outlives the plugin.
      stopFocusBadge();
    },
  };
});
