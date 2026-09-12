// Shelfcloud — add cloud games to your Steam library.
// Schelstraete Bart - https://github.com/bschelst/shelfcloud-decky

import { definePlugin, toaster } from "@decky/api";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  TextField,
  staticClasses,
} from "@decky/ui";
import { useEffect, useMemo, useState } from "react";
import { FaCloud } from "react-icons/fa";

import { getBrowsers, getCatalog, getSettings } from "./lib/api";
import { addGameToSteam, preferredProvider } from "./lib/shortcuts";
import { providerLabel, type Browser, type Game, type Settings } from "./lib/types";

const MAX_VISIBLE = 30;

function Content() {
  const [games, setGames] = useState<Game[]>([]);
  const [browsers, setBrowsers] = useState<Browser[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("Loading…");
  const [adding, setAdding] = useState<number | null>(null);

  useEffect(() => {
    void (async () => {
      const [catalog, foundBrowsers, currentSettings] = await Promise.all([
        getCatalog(false),
        getBrowsers(),
        getSettings(),
      ]);
      setBrowsers(foundBrowsers);
      setSettings(currentSettings);

      if (!catalog.ok) {
        setStatus(`Could not reach the gateway: ${catalog.error ?? "unknown error"}`);
        return;
      }
      setGames(catalog.games);
      setStatus(
        `${catalog.games.length} games${catalog.cached ? " (cached)" : ""}` +
          (foundBrowsers.length === 0 ? " — no supported browser found" : ""),
      );
    })();
  }, []);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = needle
      ? games.filter((game) => game.name.toLowerCase().includes(needle))
      : games;
    return filtered.slice(0, MAX_VISIBLE);
  }, [games, query]);

  const browserAppId = settings?.browser ?? browsers[0]?.app_id;

  async function onAdd(game: Game) {
    if (!browserAppId) {
      toaster.toast({ title: "No browser", body: "Install Chrome or Edge from Discover first." });
      return;
    }
    const provider = preferredProvider(game);
    if (!provider) return;

    setAdding(game.id);
    try {
      const { artworkApplied } = await addGameToSteam(game, provider, browserAppId);
      toaster.toast({
        title: `${game.name} added`,
        body: `Launches on ${providerLabel(provider.provider)}${artworkApplied ? "" : " (no artwork)"}`,
      });
    } catch (error) {
      toaster.toast({ title: "Could not add game", body: String(error) });
    } finally {
      setAdding(null);
    }
  }

  return (
    <PanelSection title="Cloud games">
      <PanelSectionRow>
        <TextField
          label="Search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{status}</div>
      </PanelSectionRow>

      {matches.map((game) => {
        const provider = preferredProvider(game);
        return (
          <PanelSectionRow key={game.id}>
            <ButtonItem
              layout="below"
              disabled={adding !== null}
              description={provider ? providerLabel(provider.provider) : "No provider"}
              onClick={() => void onAdd(game)}
            >
              {adding === game.id ? `Adding ${game.name}…` : game.name}
            </ButtonItem>
          </PanelSectionRow>
        );
      })}

      {games.length > matches.length && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", opacity: 0.7 }}>
            Showing {matches.length} of {games.length}. Search to narrow it down.
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "Shelfcloud",
  titleView: <div className={staticClasses.Title}>Shelfcloud</div>,
  content: <Content />,
  icon: <FaCloud />,
}));
