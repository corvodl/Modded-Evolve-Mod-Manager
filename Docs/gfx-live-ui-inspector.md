# Live UI Inspector bridge V1 (experimental, read-only)

The existing in-editor **GFX → Live UI (experimental)** tab can request a
single live Scaleform hierarchy snapshot from an authenticated companion
running inside the *exact configured, offline* Stage 2 game process.

**Implementation status:** Transport, bounded schema, fake-process adapter,
UI tab, and CI tests are implemented. **There is still no verified native
Scaleform/GFx object traversal adapter for Evolve Stage 2.** Consequently
the shipped bridge cannot currently capture a real game menu. The GFX
preview and UI_Data/UI_Assets references remain useful offline, but are
not a substitute for actual runtime hierarchy inspection.

## Security boundaries

- The bridge verifies the executable name, version and exact SHA-256 at
  startup. The manager checks the configured Evolve.exe path and the
  listening PID and requires confirmed outbound-game traffic blocking.
- It binds only to 127.0.0.1 and authenticates every session with an
  ephemeral token, HMAC-SHA256, a nonce and a bounded message.
- The new `ui_snapshot` operation is **read-only**. It does not provide
  arbitrary pointers, memory reads/writes, script execution, address
  searches, process injection, or mutations of UI objects.
- **Memory scanning, memory address reading and writing remain Developer-only**
  in the existing authenticated Developer workbench. The new GFX tab
  contains no memory editing controls. It must not provide a hidden
  bypass around that UI gate.
- Registration of the UI provider happens only from a verified adapter
  on the game thread. Network clients cannot register callbacks.
- The bridge exposes neither a loader/injector nor a game-engine ABI guess.
  No real game effects or live capture are claimed from a synthetic test.

## Native integration contract

An independently verified game-side integration should call these exported
functions *after normal module load and on the actual game thread*:

```c
int EvolveBridge_Start(const char *expected_sha256, int port);
int EvolveBridge_RegisterUiSnapshot(EvolveUiSnapshotProvider provider);
int EvolveBridge_Pump(void); /* once per game-thread tick */
void EvolveBridge_Stop(void);
```

`provider(unsigned char *output, int capacity)` must write a valid JSON UTF-8
document in the provided bounded buffer and return the byte count (0 if
unavailable). The provider may only inspect validated, still-live
Scaleform instances on the game thread; it must never retain a pointer after
returning or infer object layouts from unverified RVAs.

Example **synthetic** provider payload:

```json
{
  "schema": 1, "movie": "Libs/UI/main_menu.gfx", "frame": 1,
  "nodes": [
    {"id":"root","parent":null,"name":"MainMenu","kind":"movieclip",
     "text":"","x":0,"y":0,"width":1920,"height":1080,"visible":true},
    {"id":"title","parent":"root","name":"Featured Title","kind":"textfield",
     "text":"SYNTHETIC ONLY","x":100,"y":50,"width":260,"height":30,"visible":true}
  ]
}
```

The bridge accepts a maximum of 12,000 UTF-8 payload bytes and 128 nodes.
Each node needs a unique ID, nullable parent, type, name, text, finite bounds
and boolean visibility. Supported types are `movieclip`, `button`,
`textfield`, `sprite`, `shape`, and `unknown`. Empty snapshots are
permitted, but are not proof that there are no in-game controls.

On an authenticated loopback connection, the existing `ready` message
advertises `"ui_snapshot":"available"` **only** when a provider has
registered. Clients then send one authenticated `ui_snapshot` request
with a unique request ID. The response uses `ui_snapshot_result`,
`status=captured`, and a JSON snapshot **string**. The bridge marshals
the provider call to the game thread; requests time out after 1.4 seconds.
Malformed or unsupported requests fail closed.

## Future verified native work

1. Inspect the actual, authorized Evolve 3.6.1.2538 Scaleform integration
   to locate verified movie roots, frame bounds, instance tree iteration,
   readable text fields and proper object lifetimes. Do not assume that
   a GFX symbol name identifies a live instance.
2. Build a *read-only* native adapter that registers the above provider
   only on an exact matching build. Validate callback ABI and thread
   affinity in an isolated offline run.
3. Record sanitized snapshots across opening the menu, navigating buttons
   and changing selected game state. Compare live hierarchy with static GFX
   definitions and known ActionScript callbacks.
4. Only then consider persistent mod authoring or a separate, explicitly
   confirmed UI-editing channel. Keep arbitrary memory tools in Developer.

## Tests

- `python -m unittest test_gfx_live_ui_bridge` covers authentication,
  process/offline gates, hierarchy validation, missing providers, and
  rejected/invalid payloads without touching a running game.
- `.github/workflows/native-event-companion.yml` builds both production
  transport and a **distinct synthetic-only fixture DLL**, validates
  exports, and checks event + UI snapshot round trips through a fake
  Evolve.exe. Test artifacts are not distributable game integrations.
