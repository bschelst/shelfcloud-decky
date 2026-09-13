// Shelfcloud — your cloud games, in your Steam library.
// Schelstraete Bart - https://github.com/bschelst/shelfcloud-decky

import { addEventListener, definePlugin, removeEventListener, toaster } from "@decky/api";
import {
  ButtonItem,
  Dropdown,
  Focusable,
  Navigation,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { FaCloud } from "react-icons/fa";

import {
  forgetShortcut,
  addOwnedGames,
  clearLibrary,
  getDesiredLibrary,
  previewOwned,
  syncSteamLibrary,
  getStatus,
  logout,
  pollLogin,
  recordShortcut,
  setSetting,
  startLogin,
  syncFinished,
} from "./lib/api";
import { syncLibrary } from "./lib/sync";
import { LibrarySummary } from "./components/LibrarySummary";
import type { Status } from "./lib/types";

const SYNC_EVENT = "shelfcloud/library";
const LOGIN_POLL_MS = 2500;
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;

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

/** Run a full reconcile. Shared by the button, the poll event and post-login. */
async function runSync(onProgress?: (message: string) => void) {
  const library = await getDesiredLibrary();
  if (!library.ok || !library.desired) {
    throw new Error(library.error || "Could not read your library");
  }

  const result = await syncLibrary(library.desired, library.tracked || {}, {
    record: recordShortcut,
    forget: forgetShortcut,
    onProgress,
  });
  await syncFinished(result.added, result.removed, result.updated);
  return { result, total: library.desired.length, skipped: library.skipped ?? 0 };
}

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [owned, setOwned] = useState<number | null>(null);
  // Two-press confirm: this deletes every tile the plugin made.
  const [confirmClear, setConfirmClear] = useState(false);
  // Fraction 0..1 while syncing, so the header bar can show live progress.
  const [progress, setProgress] = useState<number | null>(null);
  const loginTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setStatus(await getStatus());
    // How many owned games could still be added, so the button can say the
    // number rather than making the user press it to find out.
    const preview = await previewOwned();
    setOwned(preview.ok ? (preview.not_added ?? 0) : null);
  }, []);

  /** Read owned games from Steam, then add every one that is on cloud. */
  const addOwned = useCallback(async () => {
    setBusy(true);
    try {
      setMessage("Reading your Steam library…");
      const steam = await syncSteamLibrary();
      if (!steam.ok) {
        setMessage(steam.message || steam.error || "Could not read your Steam library");
        return;
      }

      setMessage(`You own ${steam.owned} Steam games. Adding the cloud ones…`);
      const result = await addOwnedGames();
      if (!result.ok) {
        setMessage(result.error || "Could not add games");
        return;
      }
      setMessage(`Added ${result.added} games. Syncing to your library…`);
      await sync(true);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
      void refresh();
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // Cancel any in-flight login poll when the panel unmounts, so a forgotten
  // timer cannot keep hitting the gateway for five minutes.
  useEffect(() => () => {
    if (loginTimer.current) window.clearInterval(loginTimer.current);
  }, []);

  const sync = useCallback(async (quiet = false) => {
    setBusy(true);
    try {
      const { result, total, skipped } = await runSync((line) => {
        setMessage(line);
        // The sync emits "Adding X (12/124)"; reuse it rather than threading a
        // second callback through every call site.
        const match = /\((\d+)\/(\d+)\)\s*$/.exec(line);
        if (match) {
          const [, done, all] = match;
          setProgress(Number(all) > 0 ? Number(done) / Number(all) : null);
        }
      });
      const parts = [];
      if (result.added) parts.push(`${result.added} added`);
      if (result.removed) parts.push(`${result.removed} removed`);
      if (result.updated) parts.push(`${result.updated} updated`);
      if (result.failed) parts.push(`${result.failed} failed`);
      const summary = parts.length ? parts.join(", ") : "already up to date";
      setMessage(`${total} games — ${summary}${skipped ? ` (${skipped} skipped)` : ""}`);
      if (!quiet) toaster.toast({ title: "Shelfcloud", body: summary });
    } catch (error) {
      setMessage(String(error));
      if (!quiet) toaster.toast({ title: "Sync failed", body: String(error) });
    } finally {
      setBusy(false);
      setProgress(null);
      void refresh();
    }
  }, [refresh]);

  // NOTE: the background sync listener is deliberately NOT registered here.
  // This component only mounts while the panel is open, so a listener here
  // would make "background" sync work only while the user is watching it.
  // It lives in definePlugin() instead, which stays alive for the session.

  const signIn = useCallback(async () => {
    setBusy(true);
    setMessage("Opening Steam sign-in…");
    const start = await startLogin();
    if (!start.ok || !start.login_url || !start.session_token) {
      setMessage(start.error || "Could not start sign-in");
      setBusy(false);
      return;
    }

    // Game Mode has no browser of its own, so hand the URL to Steam.
    //
    // Navigation.NavigateToExternalWeb() opens a browser window but leaves it
    // on its blank data:text/html placeholder — verified on hardware. The
    // steam://openurl protocol actually navigates, and is the same mechanism
    // boosteroid-steamos uses for Google sign-in.
    openInSteamBrowser(start.login_url);

    setMessage("Finish signing in, then come back here…");
    const deadline = Date.now() + LOGIN_TIMEOUT_MS;
    const token = start.session_token;

    loginTimer.current = window.setInterval(async () => {
      if (Date.now() > deadline) {
        window.clearInterval(loginTimer.current!);
        setMessage("Sign-in timed out. Try again.");
        setBusy(false);
        return;
      }
      const poll = await pollLogin(token);
      if (poll.ok && poll.status === "signed_in") {
        window.clearInterval(loginTimer.current!);
        setMessage("Signed in. Syncing…");
        setBusy(false);
        await refresh();
        void sync(true);
      }
    }, LOGIN_POLL_MS);
  }, [refresh, sync]);

  /** Empty the library and let sync delete every tile we created. */
  const removeAll = useCallback(async () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setMessage("Press again to remove every Shelfcloud game from your library.");
      return;
    }
    setConfirmClear(false);
    setBusy(true);
    try {
      const result = await clearLibrary();
      if (!result.ok) {
        setMessage(result.error || "Could not clear your library");
        return;
      }
      setMessage(`Removed ${result.removed} games. Cleaning up tiles…`);
      await sync(true);
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
      void refresh();
    }
  }, [confirmClear, refresh, sync]);

  const signOut = useCallback(async () => {
    await logout();
    setMessage("Signed out. Your existing tiles were left alone.");
    void refresh();
  }, [refresh]);

  if (!status) {
    return <PanelSection title="Shelfcloud"><PanelSectionRow>Loading…</PanelSectionRow></PanelSection>;
  }

  if (!status.browsers.length) {
    return (
      <PanelSection title="Shelfcloud">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em" }}>
            No supported browser found. Install Chrome or Edge from the Discover
            store in Desktop Mode, then reopen this panel.
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (!status.signed_in) {
    return (
      <PanelSection title="Shelfcloud">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em", marginBottom: 8 }}>
            Sign in with Steam, then pick your cloud games on the website. They
            appear here automatically.
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void signIn()}>
            Sign in with Steam
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => openInSteamBrowser(status.gateway_url)}
          >
            Open the website
          </ButtonItem>
        </PanelSectionRow>
        {message && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em", opacity: 0.75 }}>{message}</div>
          </PanelSectionRow>
        )}
      </PanelSection>
    );
  }

  return (
    <>
      <PanelSection title="Shelfcloud">
        <PanelSectionRow>
          <LibrarySummary
            synced={status.tracked}
            available={(owned ?? 0) + status.tracked}
            byProvider={status.by_provider}
            progress={busy ? progress : null}
            progressLabel={message}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => openInSteamBrowser(status.gateway_url)}>
            Pick games on the website
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void sync()}>
            {busy ? "Syncing…" : "Sync now"}
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void addOwned()}>
            {owned ? `Add all my games (${owned})` : "Add all my games"}
          </ButtonItem>
        </PanelSectionRow>

        {(status.last_error || (message && !busy)) && (
          <PanelSectionRow>
            <div style={{
              fontSize: "0.8em",
              opacity: status.last_error ? 1 : 0.75,
              color: status.last_error ? "#ff6b6b" : undefined,
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

      <PanelSection title="Settings">
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
              selectedOption={status.sync_minutes}
              rgOptions={status.sync_choices.map((m) => ({
                data: m,
                label: m === 60 ? "hour" : `${m} min`,
              }))}
              onChange={(option) => { void setSetting("sync_minutes", option.data).then(setStatus); }}
            />
          </Focusable>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void removeAll()}>
            {/* ButtonItem has no destructive variant, so the label carries the
                warning colour. Brighter once armed, to mark the point of no
                return. */}
            <span style={{ color: confirmClear ? "#ff4444" : "#ff6b6b", fontWeight: confirmClear ? 700 : 500 }}>
              {confirmClear ? "Confirm — remove all tiles" : "Remove all games"}
            </span>
          </ButtonItem>
        </PanelSectionRow>

        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void signOut()}>Sign out</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  // Registered here, NOT in the panel component: Decky keeps this alive for the
  // whole Steam session, whereas the panel only exists while it is open. With
  // the listener in the component, "background" sync only ran while the user
  // was watching it — which is not background at all.
  const onLibraryChanged = () => {
    void runSync().catch((error) => {
      console.error("[shelfcloud] background sync failed", error);
    });
  };
  addEventListener(SYNC_EVENT, onLibraryChanged);

  return {
    name: "Shelfcloud",
    titleView: <div className={staticClasses.Title}>Shelfcloud</div>,
    content: <Content />,
    icon: <FaCloud />,
    onDismount() {
      removeEventListener(SYNC_EVENT, onLibraryChanged);
    },
  };
});
