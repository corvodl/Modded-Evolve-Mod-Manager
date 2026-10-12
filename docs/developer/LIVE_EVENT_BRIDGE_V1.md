# Live Game Event Activation - Companion Bridge V1 (beta)

The Developer > Game Events panel supports sending activation requests to a verified game-side module running INSIDE the selected offline Evolve.exe. Experimental Windows NativeAOT **transport and token implementation** is now available at [native/EvolveEventBridge](../../native/EvolveEventBridge/README.md). This repository still does **not** contain verified native Evolve event-dispatch handlers or a loader integrated with the game. No real-game event activation has been demonstrated.

## Important limitation

The V3 catalog is a list of event names, hashes, parameter types and emitter call sites. It is NOT a dispatcher API. The presence of an event does not show that sending it would produce an effect. Gameplay notification events are often post-action telemetry. Neither Stage2Trace.dll nor inject.dll has been verified to implement this bridge.

## Preconditions

- Developer menu unlocked; game root identifies the exact running Evolve.exe.
- Evolve-specific outbound firewall block confirmed active. Online activation is not supported even when online memory edits were separately acknowledged.
- The in-game module hosts a TCP server bound to 127.0.0.1 only, inside that Evolve.exe PID; default port 22345.
- Windows Get-NetTCPConnection must report a matching listening PID, and the executable path must match the configured game.
- The game-side module creates a fresh token (24 to 256 characters) per session and exposes it to the developer. Token is never saved by the manager.
- Game-side module explicitly advertises supported, verified V3 event names. IDs ending in a question mark are never activatable.

## Wire protocol

UTF-8 newline-separated JSON objects, 64 KiB maximum message, 2-second socket timeout. The client connects to 127.0.0.1, performs handshake, optionally sends one activation, and closes.

Every message is signed by a shared session token: remove the mac property, serialize with sorted keys, ASCII-only escapes, no whitespace and finite JSON numbers (Python json.dumps with sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False), then sign those bytes with HMAC-SHA256 using UTF-8 token bytes. The mac is the lowercase hex digest. Verify using constant-time comparison.

1. Client hello: protocol=evolve-dev-event-bridge-v1, op=hello, nonce=random 16-byte hex, pid=game PID, source=EVENTS_FOR_PARITY_V3, build=870227EC, mac=signature.
2. Server ready: same protocol, op=ready, identical nonce and pid, build=870227EC, status=ready, events=array of supported event names, mac=signature.
3. Client activate: same protocol, op=activate, identical nonce and pid, request_id=fresh random 16-byte hex, event=catalog event name, event_id=verified catalog hash, parameters=complete typed object, mac=signature.
4. Server result: same protocol, op=result, identical nonce, pid, request_id, event, status=accepted OR effect_confirmed, mac=signature. A negative response uses status=rejected and a short error string.

Game-side implementation must validate the executable version, live handles, authority, thread affinity, schema, nonce, replay protection, and per-event allowlist before making verified native engine calls. Only return effect_confirmed when the requested effect visibly or instrumentally occurred. Mere request receipt must not be reported as game activation.

## Required native work

Identify the correct dispatcher ABI and live object/handle lifetimes in the matching game build. Exe call-site RVAs are not safe callable entry points. Implement explicit verified wrappers for events or, preferably, underlying effects. Local-only and networked event metadata does not grant permission to mutate public matches.

## Testing

From source directory run: python -m unittest -v test_developer_event_catalog test_developer_event_live.

These tests cover catalog consistency, offline isolation requirements, typed payload validation, HMAC and a mock local bridge. They cannot prove effects can be activated in the real game. The native companion's separate Windows build/test checks use a synthetic executable and simulated callback; real in-game handlers and game-thread integration remain to be verified.
