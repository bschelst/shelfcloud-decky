// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import { addEventListener, definePlugin, removeEventListener, toaster } from "@decky/api";
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
import { IconLabel } from "./components/Collapsible";
import { NoBrowser } from "./components/NoBrowser";
import { SettingsSection } from "./components/SettingsSection";
import { LinksSection } from "./components/LinksSection";
import { AboutSection } from "./components/AboutSection";
import { cancelJob, cancelRequested, finishJob, getJob, runJob, setJob, subscribeJob } from "./lib/job";
import { theme } from "./lib/theme";
import type { DesiredGame, InstallState, Status, SyncResult } from "./lib/types";

const SYNC_EVENT = "shelfcloud/library";
const LOGIN_POLL_MS = 2500;
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const GATEWAY_URL = "https://shelfcloud.cc";
const GITHUB_URL = "https://github.com/bschelst/shelfcloud-decky";

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
    // Retry after a short delay — on the first call the browser window may not
    // be ready yet and lands on a blank data:text/html placeholder.
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
    shouldStop: cancelRequested,
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
async function doSync(quiet = false): Promise<boolean> {
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
    // Reported back so "add all my games" can tell a run that STOPPED from one
    // that finished, and tidy up the games it never reached.
    return Boolean(result.cancelled);
  } catch (error) {
    finishJob(String(error));
    if (!quiet) toaster.toast({ title: "Sync failed", body: String(error),
                               logo: <NotificationLogo /> });
    // A failure is not a cancel: the library is left exactly as it is so the
    // next sync can finish the job.
    return false;
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

/** Every game id currently in the account's library, or null if unreadable.
 *
 * null rather than an empty set, deliberately: "the library is empty" and "we
 * could not read it" must not be the same value, because the caller subtracts
 * this from a later reading and an empty set would make every game look newly
 * added and eligible for removal.
 */
async function libraryGameIds(): Promise<Set<number> | null> {
  try {
    const library = await getDesiredLibrary();
    if (!library.ok || !Array.isArray(library.desired)) return null;
    // Rows filtered before they are read: one null entry here throws, and the
    // catch below turns that into "could not read the library" — which is safe
    // but silently disables the cleanup this set exists for.
    return new Set(library.desired
      .filter((g) => g && typeof g === "object")
      .map((g) => Number(g.game_id))
      .filter((id) => Number.isInteger(id)));
  } catch {
    return null;
  }
}

/** After a cancelled bulk add: take back the games it added but never tiled.
 *
 * Cancel means "stop, and forget the rest". Without this the library still
 * held every owned game and the next sync simply created them all, so the
 * cancel stopped that one run and changed nothing that lasted.
 *
 * Three conditions, all required, because this REMOVES things:
 *   - not in the library before this run started, so it was added by it
 *   - has no tile, so the run never reached it
 *   - not a web page, which is mandatory and exempt from cancel anyway
 */
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

      // What was already in the library BEFORE the bulk add. Taken first so a
      // cancel can tell the games this run added from the ones that were
      // already the person's choice — only the former may be taken away again.
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

/** Empty the library and let a sync delete every tile we created.
 *
 * Wrapped in runJob so every exit leaves the panel idle. It used to set `busy`
 * itself and clear it on three of its four exits — the missing one being the
 * path where everything worked, which left every button greyed out with
 * nothing running.
 */
async function doRemoveAll(): Promise<void> {
  await runJob(async () => {
    const result = await clearLibrary();
    if (!result.ok) return result.error || "Could not clear your library";
    setJob({ message: `Removed ${result.removed} games. Removing all tiles…` });
    // Remove every tracked tile without consulting the gateway. The gateway
    // is already empty, but reading it (doSync) would re-add the website
    // tile via adopt_pushed_websites before we can remove it. An empty
    // desired list means "remove everything tracked".
    const library = await getDesiredLibrary();
    const tracked = (library.ok && library.tracked && typeof library.tracked === "object")
      ? library.tracked : {};
    const syncResult = await syncLibrary([], tracked, {
      record: recordShortcut,
      forget: forgetShortcut,
      // Cancellable. Without this the button said "Stopping…" and went on
      // deleting every remaining tile, because the stop was only ever checked
      // in the ADD loop and this run has nothing to add.
      shouldStop: cancelRequested,
    });
    await syncFinished(syncResult.added, syncResult.removed, syncResult.updated);

    if (syncResult.cancelled) {
      // Stopping the deletions is not enough on its own. The library was
      // emptied server-side before any tile was touched, so every tile the
      // cancel just saved is no longer wanted and the next sync would take
      // it — the stop would have lasted about a minute. Put the survivors
      // back, and only those: a game whose tile is already gone stays gone,
      // because undoing a deletion somebody watched happen is worse than
      // honouring it.
      //
      // Read back rather than reusing `tracked`: the removal loop tells the
      // backend to forget each tile as it goes, but does not mutate the copy
      // it was handed. Using that copy would restore the games whose tiles
      // were successfully deleted — the exact opposite of honouring them.
      const after = await getDesiredLibrary().catch(() => null);
      const survivors = Object.keys(
        (after?.ok && after.tracked && typeof after.tracked === "object")
          ? after.tracked : {},
      ).map(Number).filter((id) => Number.isInteger(id));

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

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [owned, setOwned] = useState<number | null>(null);
  // Which shelf this device shows, and what it could show. Loaded from the
  // gateway rather than from settings: the shelf can be changed on the website
  // or another device, so a local copy would go quietly out of date.
  const [shelves, setShelves] = useState<Awaited<ReturnType<typeof getShelves>> | null>(null);
  // Two-press confirm: this deletes every tile the plugin made.
  const [confirmClear, setConfirmClear] = useState(false);
  // Cancel has been pressed for the run in flight. Local to the panel rather
  // than in the job store because it is only about THIS button's label: the
  // run stops between tiles, so there is a gap between asking and stopping,
  // and a button that still says "Cancel" invites a second press.
  const [stopping, setStopping] = useState(false);
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

  // Put the Cancel button back to its normal label once the run it belongs to
  // has actually stopped, so the next one does not open already saying
  // "Stopping…".
  useEffect(() => { if (!busy) setStopping(false); }, [busy]);

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
                  const url = (status as unknown as Record<string, unknown>).pending_login_url as string || GATEWAY_URL;
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
          <ButtonItem layout="below" disabled={busy || signedOut} onClick={() => void addOwned()}>
            {owned ? `Add all my games (${owned})` : "Add all my games"}
          </ButtonItem>
        </PanelSectionRow>

        {/* While something is running this row becomes the way OUT of it.
            "Add all my games" walks a whole Steam library — thousands of tiles
            and many minutes on a large account — and until now the only ways
            to stop were to let it finish or to kill Steam. The button is only
            replaced while busy, so there is never a Cancel with nothing to
            cancel. */}
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

      <PanelSection>
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
