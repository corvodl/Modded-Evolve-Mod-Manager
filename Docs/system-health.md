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


## PR #81 follow-up additions

- **Pre-launch safety checks** are invoked only after a user requests a
  launch. Game executable, active process, administrator status and swap
  journal are checked read-only. Hard failures block launch; diagnostic
  advisory notes go to the Activity log. The existing signed-stage and
  outbound firewall checks are still authoritative. There is no periodic
  launch polling, auto-restoration or automatic permission change.
- **Discord-ready Support Report** is composed only from the five cached
  System Health summaries, client/game versions, OS and optional recent
  event summaries. It excludes diagnostic detail bodies and logs. An editable
  privacy preview is shown before Copy or Save Text. Local and network
  paths, emails and common password/token patterns are redacted again on
  copy or save. Users should review and remove anything else sensitive.
- **Windows Crash History** runs a bounded (15-second) read-only query against
  the Windows Application Event Viewer. It shows matching Evolve.exe and
  ModdedEvolveLauncher.exe crash/error entries only, with timestamps and
  faulting module when available. It never modifies Event Viewer settings.
- **Project table** groups independent editable workspaces by exact target
  PAK with collapsible groups and lightweight in-memory filtering. The
  actual folder name is used, with full workspace and existing project
  label in the tooltip, changed-file count, source build and filesystem
  last-modified date. Grouping does not merge, move or rename projects.
- **Contextual recovery** only appears when a scan identifies unsafe or
  interrupted swap states. It navigates to Settings and focuses Restore
  Game Files, with no automatic recovery or file deletion.
- **Compact display** uses one collapsed panel, an overall checked/attention
  summary and check time. Advanced privileged actions are in per-section
  Actions menus, so they do not expand the Home page's width.

Validation adds test_system_health_extras and test_qt_system_health_polish to
the Windows Qt and distribution regression lists. Real UAC, WER minidump
capture and launch protection still require an actual Windows/game test.
