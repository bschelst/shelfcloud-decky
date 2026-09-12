// Calls into the plugin's Python backend (main.py).

import { callable } from "@decky/api";

import type { LibraryResponse, LoginPoll, LoginStart, Status } from "./types";

export const getStatus = callable<[], Status>("get_status");
export const setSetting = callable<[key: string, value: unknown], Status>("set_setting");

export const startLogin = callable<[], LoginStart>("start_login");
export const pollLogin = callable<[session_token: string], LoginPoll>("poll_login");
export const logout = callable<[], { ok: boolean }>("logout");

export const getDesiredLibrary = callable<[], LibraryResponse>("get_desired_library");
export const recordShortcut =
  callable<[game_id: number, app_id: number, name: string, options: string], unknown>("record_shortcut");
export const forgetShortcut = callable<[game_id: number], unknown>("forget_shortcut");
export const syncFinished =
  callable<[added: number, removed: number, updated: number], unknown>("sync_finished");
