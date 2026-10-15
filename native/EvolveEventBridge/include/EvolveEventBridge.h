#pragma once
/* Companion ABI (Win64). All calls are made INSIDE the game's process.
   No injector and no unverified Evolve dispatcher are provided.
   ABI is Cdecl (standard Win64 calling convention).
*/
#ifdef __cplusplus
extern "C" {
#endif

/* Native handler return values: 0 rejected, 1 request accepted,
   2 confirmed effect. Only return 2 after a real observed effect. */
typedef int (__cdecl *EvolveVerifiedEventHandler)(const char *event_name,
                                                   const char *parameters_json);

/* Returns 1 on success. MUST be invoked after LoadLibrary returns, on the
   engine game thread. expected_sha256 must be 64 hex chars for the verified
   Evolve.exe; the bridge validates the executing process and file version. */
int __cdecl EvolveBridge_Start(const char *expected_sha256, int port);

/* Register only validated, explicitly supported native handlers. schema_json:
   {"source":"entity","active":"bool"}; supported types: entity, string,
   bool, int, float. Must run on the game thread. No arbitrary call RVAs. */
int __cdecl EvolveBridge_RegisterVerifiedEvent(const char *name,
   const char *event_id, const char *schema_json,
   EvolveVerifiedEventHandler handler);

/* Call regularly from a verified game-thread callback; this is the ONLY
   place native event handlers execute. Returns events processed. */
int __cdecl EvolveBridge_Pump(void);

/* Copies token into a game-side buffer (buffer length >= 65), including NUL.
   Never call this export from an untrusted injected module. */
int __cdecl EvolveBridge_CopyToken(char *buffer, int capacity);

/* Shut down before unloading native DLL, outside DllMain. */
void __cdecl EvolveBridge_Stop(void);

#ifdef __cplusplus
}
#endif
