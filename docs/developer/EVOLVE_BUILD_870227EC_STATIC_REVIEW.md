# Static inspection: Evolve build 870227EC and Stage2Trace (2026-10-12)

This is a **static-only assessment** of binaries voluntarily provided for private
Evolve Stage 2 modding. Neither image was run, injected or decrypted.
Neither game binary is committed or redistributed.

## Confirmed binary identity

| Attribute | Evolve.exe | Stage2Trace.dll |
|---|---|---|
| Machine | AMD64 / PE32+ (0x8664) | AMD64 / PE32+ (0x8664) |
| Size (bytes) | 57,009,728 | 2,244,096 |
| PE timestamp | 0x580e5f05 (2016-10-24) | 0x6ac8a113 (2026-10-09) |
| Image base | 0x140000000 | 0x180000000 |
| SHA-256 | `870227ec5ef4c2f020e6c825d13a137cc03c7a23ea55362782dc29a96127737d` | `cdd80905014985276126c9dc2c1e83dc011a72c092c1cb9c06479c18ba510a1b` |

The first eight hexadecimal characters of the Evolve executable hash match the
existing V3 event catalog's `870227EC` build identifier. The executable's
reported version is `3.6.1.2538` (already verified by existing game support
logs). The **full digest** is the required match for the production companion,
not just the eight-character catalog identifier or the file version.

## Game executable is protected on disk

The executable contains two code sections both named `.text`:

| Section RVA | Raw size | First 1 MiB Shannon entropy |
|---|---|---|
| `0x1000` | `0x21f2a00` | ~8.000 bits/byte |
| `0x3bd7000` | `0xa6ca00` | ~7.980 bits/byte |

For comparison, the supplied Stage2Trace DLL's `.text` section has ~5.375
bits/byte entropy over the same sample limit.

Disassembling the on-disk Evolve bytes at cataloged event send sites such as
`RVA 0x14EE447` (local highlights) produces inconsistent, nonsensical
instruction sequences. This is **consistent with encrypted / obfuscated
on-disk code**. It is **not proof of the specific packer or encryption
algorithm**, but it means these RVAs cannot be verified as callable native
entry points by static disassembly of the uploaded executable.

The cataloged send sites are also **not** engine dispatcher signatures:
they may be call sites or event/telemetry notifications. Dispatching the
named notification may have no effect on actual gameplay.

## Stage2Trace integration findings

- The supplied DLL has **no PE export directory**, hence no exported
  `EvolveBridge_Start`, event-dispatch or public extension API.
- It has substantial internal native hook machinery (DLL strings mention
  signature matching, native hooks, profile-compatibility, P2P/listener and
  scene/menu hooks), but its internal C++ signatures are private and cannot
  be assumed compatible with the new bridge.
- Searching both supplied images for the `evolve-dev-event-bridge-v1`
  protocol, `EvolveBridge_Start` export label and example names
  `local_highlights`, `weapon_shoot` found no corresponding ASCII or
  UTF-16LE literals.
- These results do **not** prove the game lacks an event dispatcher; they
  mean that the uploaded static images do not provide a verified, callable
  dispatcher ABI.

## Companion readiness

- `native/EvolveEventBridge` now **pins the production DLL to this exact
  Evolve.exe SHA-256**, rejecting all other executable hashes even if a caller
  claims they are compatible.
- Session token generation, localhost authentication, typed allowlist,
  main-thread callback queuing and timeouts are implemented and tested against
  a **synthetic Evolve fixture**, not a real game session.
- **No real-game event handler has been identified or registered.** The
  production module advertises zero events until such a handler exists. Its
  synthetic test build is a separate CI-only variant and must never ship.

## Required to establish real activation

1. Obtain a **runtime-decrypted, legally obtained code-region trace** of this
   specific executable after game initialization, ideally with a debugger
   breakpoint at a naturally raised, locally scoped event and adjacent
   disassembly. Do **not** upload process-wide memory containing accounts,
   authentication tokens, other players' data or unrelated private content.
2. Identify whether the observed send site actually modifies gameplay state
   or is only a notification, and follow the call chain to an effect handler.
3. Record a verified Windows x64 ABI, valid game-thread context, proper
   entity/parameter representation, object lifetimes, and a safe way for
   the in-game integration to call `EvolveBridge_Pump`.
4. Prove offline isolation and observe before/after state in a local match
   with bots. Register only validated event wrappers. An authenticated
   packet or a callback return code alone is **not** evidence of an effect.
5. Do not attempt any guessed on-disk RVA calls, inject into live online
   matches, or mark this feature as available in a released installer.

**Verdict:** Both supplied binaries can be identified and used for build
pinning and protocol design. They are insufficient to claim verified
in-game event activation. The experimental PR remains unmerged.
