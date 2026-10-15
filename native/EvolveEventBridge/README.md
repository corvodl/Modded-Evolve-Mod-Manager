# Evolve game-side event companion (experimental source)

This project builds `EvolveEventBridge.dll` as a Windows x64 **NativeAOT**
native DLL. It has no external .NET runtime requirement once published.

**Status: transport and game-thread callback infrastructure ONLY.** The
repository still has **no verified Evolve 3.6.1.2538 native event-dispatch ABI**.
No event is activatable in the actual game until a separate verified game-side
adapter registers handlers and invokes `EvolveBridge_Pump` on the engine
thread. This is deliberately not an RVA-calling engine hook, injector or
arbitrary memory-write library.

## Build

From Windows with .NET SDK 8 and Visual Studio 2022 C++ Build Tools:

```powershell
dotnet publish native/EvolveEventBridge/EvolveEventBridge.csproj -c Release -r win-x64 -o dist/native-bridge
```

The `EvolveEventBridge.dll` output is a native library, *not* a standalone
injection mechanism. Do not distribute it as a working gameplay-event mod.

## Native integration contract

Include `include/EvolveEventBridge.h`. A trusted, independently verified
in-game integration must:

1. Identify `Evolve.exe` file version **3.6.1.2538** and validate its exact
   SHA-256 against an approved, game-build-specific digest. The C API
   `EvolveBridge_Start(digest, 22345)` repeats both checks inside the game
   process and refuses to start on mismatches. It must be invoked on the real
   game thread **after** native DLL loading completes, never in `DllMain`.
2. Verify offline isolation and the active game session. The manager also
   enforces a verified outbound Evolve firewall block before connecting.
   Localhost transport is not proof that a networked game match is offline.
3. Register a handler using
   `EvolveBridge_RegisterVerifiedEvent(name, event_id, schema, callback)`,
   only after the callback's ABI, native object lifetimes, field mapping,
   authority, and event effects are independently confirmed for this build.
   The schema is a JSON object mapping every required property name to
   `entity`, `bool`, `int`, `float` or `string`. Unverified event IDs
   (those ending with `?`) and unsupported events must not be registered.
4. Call `EvolveBridge_Pump()` once per game thread update. Native handlers
   are not invoked from the loopback network thread. Callback return codes:
   0 = rejected, 1 = accepted (effect not yet confirmed), and 2 = effect
   confirmed by the verified game-side handler.
5. On teardown, call `EvolveBridge_Stop()` outside `DllMain`; the listener
   closes and session credentials are discarded.

The bridge advertises **only registered event names**. There are zero
registered handlers by default. A successful authenticated connection does
*not* prove that any particular event effect can be produced.

## Retrieving the session token

After a successful `Start`, the bridge creates a cryptographically random,
64-character hex token for that session. It writes a one-time message using
Windows `OutputDebugStringW`:

```text
[EvolveEventBridge] PID <pid> session token: <64-char-hex-token> (local offline only)
```

An authorized developer may see that output in **DebugView** or an attached
debugger and paste the token into the manager's Developer > Game Events panel.
An existing in-game trusted module can alternatively call
`EvolveBridge_CopyToken` from the game thread (requires a buffer >= 65
bytes), and present it through its own private developer UI. The manager
never stores the token. Do not share it or paste it into publicly accessible
crash logs.

The same protocol and authentication requirements are documented in
[`LIVE_EVENT_BRIDGE_V1.md`](../../docs/developer/LIVE_EVENT_BRIDGE_V1.md).

## What is still necessary for real Evolve activation

- The matching executable and associated native module(s) for permitted
  reverse engineering; the source here does not include copyrighted game
  binaries.
- Trace and validate **real dispatcher entry points** and whether they
  execute effects or merely publish post-action telemetry notifications.
- Confirm calling conventions, game-thread identity, typed event payload ABI,
  entity-handle ownership/validity, permissions and side-effect confirmation.
- Test effects in an isolated offline session, with logs comparing target
  state before and after each handler; never enable the bridge in online games.

No release label or "successful activation" report should be published until
those native integration checks succeed.
