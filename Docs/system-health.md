# System Health (Home)

The **System Health** disclosure is collapsed at startup. Expanding it triggers
one on-demand, off-GUI-thread read-only scan; **Check System Health** runs it
again. There are no polling timers, popup notifications, startup registry
changes or background cleanup.

Five compact rows provide:

1. **Firewall** — read-only status for the existing exact-path manager-owned
   EvolveModManager-Offline-* **outbound block** rule for Evolve.exe.
   *Enforce Offline Block* and *Remove Manager Block* require confirmation.
   Evolve/launcher must be stopped, and the normal modded launch path remains
   responsible for enforcing offline isolation. No unrelated firewall rule
   is added, disabled, or deleted.
2. **Game Process** — Evolve and launcher PID, sampled CPU, resident memory,
   and uptime, from bundled psutil. Only samples on demand.
3. **Crash Dumps & Support** — opt-in **Evolve.exe-only** WER LocalDumps key
   (HKLM). DumpType is minidump, maximum 5 dumps, stored in the manager's
   own Data/CrashDumps folder. Existing externally managed WER settings are
   left untouched; disabling only removes known manager-owned values if they
   still have the exact expected values. Manual dump capture requires a
   running Evolve.exe, a destination, and confirmation. Support ZIP contains
   the read-only report and bounded .log files, **never** memory dumps,
   original PAKs, game executables, private settings, or cryptographic keys.
   Users should still review log content before sharing it.
4. **Storage & Backups** — free space on project/staging/swap volumes, swap
   journal health, outstanding original backups, and count of stale partial
   downloads. Confirmed cleanup only removes files with .tmp, .part,
   .partial extensions older than 24 hours inside manager-owned
   Data/Temp and Data/Downloads. Original game files, projects, and
   backup snapshots are never cleanup targets.
5. **Game Environment** — presence of Evolve.exe, selected VC++/DirectX
   DLLs, and Windows version; component presence is a diagnostic hint, not
   a claim that the runtimes or game are healthy. See the separate firewall
   row for outbound protection.

The packaged GUI requests UAC approval at launch; helpers do not. Every
mutating System Health action still requires its own in-app confirmation,
and a declined dialog does nothing. Read-only information is available
without changing system settings.

## Validation

Run: python -m unittest -v test_system_health test_qt_project_lifecycle_admin

Windows Qt CI executes these tests in offscreen mode. Packaging is checked by
Build-Windows-EXE.cmd; real-game dump capture, UAC consent, and firewall
enforcement must also be exercised on an actual Windows test install.
