# Decky RPC payload limits — the 4 MB rule

## Status

Fixed and guarded in code. **Read this before adding or changing any RPC.**

## The rule

> **No single Decky RPC message may exceed 4 MB, in either direction.**
> Large data is sliced, or named by URL and fetched on the Python side.

Breaking this rule does not produce an error. It produces a **frozen plugin
with an empty log**, and it has cost multiple days of debugging on two separate
occasions.

## Why it is silent

Every RPC in this plugin — all of them, in both directions — rides **one**
aiohttp websocket that Decky Loader opens per renderer context
(`ws://127.0.0.1:1337/ws?auth=…`). `aiohttp.WebSocketResponse` defaults to
`max_msg_size = 4 MB`, and on a larger message it **closes the connection
rather than raising**.

That single fact produces a failure that erases its own evidence:

| What you expect | What actually happens |
|---|---|
| An exception naming the oversized call | Nothing is raised, anywhere |
| The call rejects | The promise **never settles** |
| A timeout reports it | The timeout's own `logNote` RPC needs the same dead socket, so it cannot be written |
| The log names the last thing tried | The log ends **mid-sentence** |
| One call fails | **Every** outstanding and future call on that socket hangs |

So the symptom is "the plugin is stuck on Loading…", and the natural instinct —
find the call that hangs — is wrong, because by the time you look, *all* calls
hang. The oversized one is already gone from view.

The loader's own journal (`journalctl -u plugin_loader`) is the only place that
records it, as:

```
[wsrouter][DEBUG]: Websocket connection closed
[wsrouter][DEBUG]: Dropping message as there is no connected socket
```

The plugin's own log file (`~/homebrew/logs/Shelfcloud/*.log`) never sees these.
Note that reading the journal needs real root — `sudo -n` fails silently here
and an empty grep is a **false negative**, not evidence of a healthy socket.

## The two incidents

### 1. Backend → frontend: artwork (`fetch_artwork`)

Steam hero art is 6–7 MB, which is ~9 MB once base64-encoded. The **first hero
of every sync** killed the socket. It appeared to land on a different game each
run — whichever one happened to need artwork first — which is what made it look
intermittent.

**Fix:** slice it. `ARTWORK_CHUNK_CHARS = 192 * 1024` in `main.py`; the
frontend reassembles in `fetchArtworkBase64`, using the reply's `total` to know
when it has all of it. The backend holds the last image in `_artwork_part` so
the follow-up slices do not re-download the same picture.

### 2. Frontend → backend: the shortcut icon (`save_icon`) — 2026-09-26

`save_icon` took the image as base64, so the frontend pushed the whole picture
**back** over the socket. This was the only large message travelling in that
direction and the only one never sliced — the asymmetry hid it, because the
artwork path had been correct for months.

It was not a rare image that triggered it. The icon uses a fallback chain:

```
icon slot  ->  logo slot  ->  capsule
```

Any game shipping no icon of its own falls through to its **capsule**, which is
600×900. So the sync died on the **first game of every run**, having logged
every step before `saveIcon` and nothing after.

**Fix:** `save_icon_from_url(game_id, url, app_id)`. The frontend sends a URL —
a few hundred bytes — and the backend writes the file from the image it already
holds in `_artwork_part`, falling back to fetching it itself. No bytes cross the
socket and there is no second download. `save_icon` remains only for images
**drawn** locally (a capsule rendered from the game's title), which have no URL
to name and are a few kilobytes of flat colour and text.

## Guard rails now in the code

Three, in `src/lib/rpc.ts` and `main.py`. They do not replace the rule — they
make breaking it loud instead of silent.

| Guard | Where | What it does |
|---|---|---|
| `MAX_ARG_CHARS = 3 MB` | `rpc.ts`, `tooBig()` | Refuses to send any string argument over the cap, with an error naming the call and the size. Checked **before** the socket, because this is the one failure that takes the socket with it. |
| `CALL_DEADLINE = 60 s` | `rpc.ts`, `onADeadline()` | Rejects any call still outstanding past the deadline, so it always gives its concurrency slot back. |
| `ARTWORK_CHUNK_CHARS = 192 KB` | `main.py` | Every artwork reply is sliced to well under the cap. |

The deadline matters as much as the size check, and for a reason that is easy to
miss: `withTimeout` gives up **waiting** on a promise but cannot **cancel** it,
so an abandoned call still sat inside `withSlot` holding its permit. With
`MAX_IN_FLIGHT = 4`, four such calls meant every later call — including the
panel's own `get_status` — queued behind permits that were never coming back.
That leak is what turned one hung call into a permanently frozen panel.

## Checklist for a new RPC

- [ ] Could any argument or return value exceed ~1 MB? If yes, **stop** — slice
      it, or pass a URL/path and do the I/O on the Python side.
- [ ] Is a large payload crossing the socket that the **other side already
      has**? Name it instead of shipping it. This was the whole icon bug.
- [ ] Is it declared through `guarded()` in `src/lib/api.ts`? Nothing should
      call `callable()` directly — that bypasses the size check, the deadline
      and the concurrency limit.
- [ ] Does it do blocking I/O in the backend? Put it on a thread
      (`asyncio.to_thread`); the event loop answers every other RPC, including
      the one the panel uses to ask whether it is signed in.
- [ ] Never pass `timeout=None` to `urllib3` — it means *wait forever* and
      overrides the pool default, which starves the thread pool.

## How to diagnose a stall like this again

Bisect with `phase()` markers, not by reasoning about which call looks risky.
`phase()` (`src/lib/sync.ts`) writes a named marker to the plugin log through a
bounded `logNote`, and the last marker printed names the hanging statement
exactly. Add them around every call in the suspect block, install, run a sync,
read the tail:

```bash
L=$(ls -t ~/homebrew/logs/Shelfcloud/*.log | head -1)
grep "frontend: phase" "$L" | sed "s/.*phase: //" | tail -10
```

Then remove the fine-grained ones once the cause is found — at ~10 markers per
game they are ~9,000 extra RPCs on an 898-game library. The seven coarse phases
stay.

Two cautions learned the hard way:

- **A marker that never prints does not mean the code never reached it.** If the
  socket is already dead, the marker cannot be written either. The last marker
  is where it *stopped being able to report*, which is the same statement only
  when the socket died there.
- **An unbounded synchronous Steam call cannot be timed out.** `SetShortcut*`
  and friends were ruled out this way; they were not the cause, but they are the
  only calls here that no deadline can cover.

## Related

- `docs/CONTROLLER-LAYOUTS.md` — the layout install path, which also writes
  files from the backend rather than shipping them over the socket
- `tests/frontend/rpc.test.ts` — the guards above, as tests
- `tests/unit/test_save_icon.py` — saving an icon by reference
