// Calls into the plugin's Python backend (main.py).

import { callable } from "@decky/api";

import type { Browser, CatalogResult, Settings } from "./types";

export const getCatalog = callable<[force: boolean], CatalogResult>("get_catalog");
export const getBrowsers = callable<[], Browser[]>("get_browsers");
export const getSettings = callable<[], Settings>("get_settings");
export const setSetting = callable<[key: string, value: unknown], Settings>("set_setting");
