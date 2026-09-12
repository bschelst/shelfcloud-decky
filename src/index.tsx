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
  getDesiredLibrary,
  getStatus,
  logout,
  pollLogin,
  recordShortcut,
  setSetting,
  startLogin,
  syncFinished,
} from "./lib/api";
import { syncLibrary } from "./lib/sync";
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
  const loginTimer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    setStatus(await getStatus());
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
      const { result, total, skipped } = await runSync(setMessage);
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
      void refresh();
    }
  }, [refresh]);

  // The backend polls the gateway and fires this; only the frontend can touch
  // SteamClient, so the actual reconcile happens here.
  useEffect(() => {
    const handler = () => { void sync(true); };
    addEventListener(SYNC_EVENT, handler);
    return () => { removeEventListener(SYNC_EVENT, handler); };
  }, [sync]);

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
      <PanelSection title="Library">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em" }}>
            {status.tracked} game{status.tracked === 1 ? "" : "s"} synced into your
            Shelfcloud collection.
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={() => openInSteamBrowser(status.gateway_url)}
          >
            Pick games on the website
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void sync()}>
            {busy ? "Syncing…" : "Sync now"}
          </ButtonItem>
        </PanelSectionRow>
        {message && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em", opacity: 0.75 }}>{message}</div>
          </PanelSectionRow>
        )}
        {status.last_error && (
          <PanelSectionRow>
            <div style={{ fontSize: "0.8em", color: "#ff6b6b" }}>{status.last_error}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Settings">
        <PanelSectionRow>
          <Focusable style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: "0.8em", opacity: 0.75 }}>Browser used to play</div>
            <Dropdown
              selectedOption={status.browser?.app_id}
              rgOptions={status.browsers.map((b) => ({ data: b.app_id, label: b.label }))}
              onChange={(option) => {
                void setSetting("browser", option.data).then(setStatus);
              }}
            />
          </Focusable>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void signIn()}>
            Sign in again
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void signOut()}>
            Sign out
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: "0.75em", opacity: 0.6 }}>
            {status.gateway_url}
          </div>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "Shelfcloud",
  titleView: <div className={staticClasses.Title}>Shelfcloud</div>,
  content: <Content />,
  icon: <FaCloud />,
}));
