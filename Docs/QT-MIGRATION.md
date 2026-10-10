# PySide6 / Qt migration preview

**Status: Experimental, isolated from the Main release.** This branch develops a
native Qt frontend while keeping the existing Tkinter Main app intact.

## What's migrated

- The complete five-page manager shell: Instructions, Modding, Play & Restore,
  Settings and Credits; application icon/version/status and detailed Activity.
- Native Qt table: filename-derived archive descriptions, file filters, sorting,
  multi-selection, right-click inspection/unpack/reveal/copy/location actions.
- Existing worker-backed extract, batch unpack, diff, build, stage, import,
  undo, local setup, complete bundle configuration/export, launch, journal
  restoration, orphan backup recovery and original PAK refresh actions.
- Native Qt extracted workspace editor: text/CryXML saving (structural validation
  and backups), model inspection/preview, texture previews, BCn PNG/DDS import,
  streamed DDS replacement, file import/export and original restoration.
- Native Qt loose-file editor, preserving the project-copy-only guarantee.
- Animated on-demand navigation driven by QPropertyAnimation, not repeated Tk
  widget placement. The content never resizes when the menu opens.
- Advanced GPU/material/model tools remain available through a **separate**
  bundled compatibility editor (EvolveModLegacyEditor.exe) while those
  remaining renderer features are migrated. The Qt editor explicitly labels
  this control rather than silently omitting it.

## What is not yet at native Qt parity

The original GPU-accelerated model rendering and all specialist model-material
controls are still provided by the existing Tk editor, launched as a separate
process. They are **not** claimed to have been fully ported.

The Qt preview intentionally does **not** apply the stable Main updater
manifest to a different executable. The production Main-channel updater stays
unchanged until a Qt-specific update and rollback path has been tested.

## Building and running

On a Windows x64 development machine with Python 3.11, run
Build-Windows-Qt-Preview.cmd. It installs the Qt dependency into an isolated
build environment and creates dist/EvolveModManagerQt-Preview-Windows.zip.
The preview bundle includes the Qt manager, worker, and advanced legacy editor.
Do not replace the stable EvolveModManager directory with this preview.

The qt-migration GitHub Actions build uploads the preview ZIP and a genuine
screenshot obtained from the **compiled Qt exe**. No assets are published to
the stable Main release channel.

**Testing with existing user Data:** For isolation, extract the preview in a
different folder. It uses its own Data folder unless you deliberately point
it at an existing stage and projects directory. Never modify installed game
files while another manager instance has a swap active.

## Parity requirement

Qt will become the default only once the GPU model preview, full editor
controls, worker management, updater rollback, packaging, and game restore
tests have all passed. Features left accessible via compatibility UI must
remain explicitly identified until the native replacements are verified.
