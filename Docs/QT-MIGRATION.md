# Qt migration and Windows release candidate

The Evolve Mod Manager main window, menus, instructions, Help, Credits,
PAK browser, project editors, settings, update controls, and Play/Restore
commands have been migrated to PySide6.

## Native Qt features

- On-demand animated overlay navigation; no shifting the PAK list.
- Readable and sortable PAK names and description with loose-game-file support.
- Multi-PAK extraction and isolated workspace selection.
- Verified PAK extraction, signing, build/install/import, rollback and backup
  recovery through the existing worker backend.
- Native Files / Text (UTF-8 / CryXML), Images / DDS (including streaming
  fragments and PNG re-encoding), Models and loose project-file editors.
- Native Qt OpenGL model viewer: rotate, zoom, wireframe, quality,
  approximate texture UV mapping and material lookup. Non-OpenGL systems can
  still use the static Qt mesh preview.
- Full original instructions, help topics including offline-gameplay safety,
  restoration guidance, credits and advanced configuration fields.
- Desktop shortcut with original red/black icon.
- Stable-only automatic updater; manual Check for Updates additionally
  discovers optional successful experimental Qt builds.

The old Tk UI is not an entry point for the candidate production app. Legacy
Tk source remains in the repository to preserve the backend and run regression
tests while comparing old and new implementations.

## Safety and packaging

- The mod manager must NOT include Evolve or private keys.
- Launch/restoration journal and original files must never be silently reset.
- Preserve the Data directory when updating or moving the portable manager.
- New UI builds only replace the existing main release after passing the
  backend suite, Qt parity tests, frozen Windows worker smoke test, real
  executable screenshot and isolated ZIP verification.
- The qt-migration branch builds are experimental even if they carry the
  production-shaped executable; the Main release tag is not changed by
  experimental workflows.

## Current validation

Qt GUI controls and editor saves are covered by automated parity smoke tests.
Backend tests are retained unchanged. Native OpenGL requires a working graphics
driver and needs additional real-game device testing. No UI rewrite can
guarantee gameplay or Evolve network compatibility.
