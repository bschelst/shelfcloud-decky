# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Read Steam's shortcuts.vdf and find the tiles this plugin made.

The plugin knows which shortcuts it created because it writes them down. When
that record and reality disagree, it has no way to notice: reconcile removes
"tracked but no longer wanted", and a shortcut it never tracked is invisible to
it. That is how a game ends up on the shelf twice — one tile tracked, one
orphaned, and nothing that will ever look at the second.

The orphan is identifiable. Every Shelfcloud shortcut launches
shelfcloud-launch.py with `--game-id N` in its options, so the file itself says
which game each tile belongs to.

Reading rather than writing, deliberately. Steam holds shortcuts.vdf open and
rewrites it wholesale; editing it underneath a running client loses whatever it
flushes next. Removing a tile is SteamClient's job — this only finds them.

Binary VDF, enough of it for this:
    0x00 key \0   a nested map begins
    0x01 key \0 value \0   string
    0x02 key \0 <int32 little-endian>   number
    0x08   end of the current map
Keys are case-inconsistent across Steam versions ("AppName", "appname"), so
everything is matched case-insensitively.
"""

import os
import re
import struct
from typing import Dict, List, Optional

# Every shortcut this plugin creates runs the launcher with this flag.
_GAME_ID = re.compile(rb"--game-id\s+(\d+)")

_APPID = re.compile(rb"\x02appid\x00(....)", re.IGNORECASE | re.DOTALL)
_OPTIONS = re.compile(rb"\x01LaunchOptions\x00([^\x00]*)\x00", re.IGNORECASE)
_NAME = re.compile(rb"\x01AppName\x00([^\x00]*)\x00", re.IGNORECASE)

# Entries are keyed by their index: 0x00 "0" 0x00, 0x00 "1" 0x00, and so on.
_ENTRY = re.compile(rb"\x00(\d+)\x00")


def shortcuts_files(steam_root: Optional[str] = None) -> List[str]:
    """Every user's shortcuts.vdf on this machine.

    One per Steam account, and a Deck can have several. The caller does not
    know which account is signed in, so all of them are read and the app ids
    speak for themselves.
    """
    root = steam_root or os.path.expanduser("~/.steam/steam")
    userdata = os.path.join(root, "userdata")
    found = []
    try:
        for user in os.listdir(userdata):
            path = os.path.join(userdata, user, "config", "shortcuts.vdf")
            if os.path.isfile(path):
                found.append(path)
    except OSError:
        pass
    return found


def parse(blob: bytes) -> List[Dict]:
    """Every Shelfcloud shortcut in one shortcuts.vdf, as {app_id, game_id, name}.

    Entries are split on the index markers rather than parsed as a tree: the
    file is Steam's, its exact shape varies between client versions, and a
    tolerant scan that can miss a malformed entry is much better here than a
    strict parser that raises on one and reports nothing at all.
    """
    out: List[Dict] = []
    if not blob:
        return out

    marks = [m.start() for m in _ENTRY.finditer(blob)] + [len(blob)]
    for start, end in zip(marks, marks[1:]):
        chunk = blob[start:end]

        options = _OPTIONS.search(chunk)
        if not options:
            continue
        game = _GAME_ID.search(options.group(1))
        if not game:
            continue        # somebody else's shortcut

        appid = _APPID.search(chunk)
        if not appid:
            continue
        # Steam stores it as an unsigned 32-bit id; Python wants the same back.
        (app_id,) = struct.unpack("<I", appid.group(1))

        name = _NAME.search(chunk)
        out.append({
            "app_id": app_id,
            "game_id": int(game.group(1)),
            "name": name.group(1).decode("utf-8", "replace") if name else "",
        })
    return out


def shelfcloud_shortcuts(steam_root: Optional[str] = None) -> List[Dict]:
    """Every Shelfcloud tile Steam currently has, across all accounts."""
    found: List[Dict] = []
    for path in shortcuts_files(steam_root):
        try:
            with open(path, "rb") as handle:
                found.extend(parse(handle.read()))
        except OSError:
            continue
    return found


def duplicates(shortcuts: List[Dict]) -> Dict[int, List[int]]:
    """game_id -> its app ids, for games that have more than one tile.

    Order is the order Steam stores them, so a caller keeping "the first" keeps
    the oldest — the one more likely to hold playtime and a controller layout.
    """
    by_game: Dict[int, List[int]] = {}
    for entry in shortcuts:
        by_game.setdefault(entry["game_id"], []).append(entry["app_id"])
    return {game: ids for game, ids in by_game.items() if len(ids) > 1}
