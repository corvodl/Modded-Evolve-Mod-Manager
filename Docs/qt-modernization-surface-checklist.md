# Qt GUI modernization — surface review checklist

The icon-led navigation and Updates-first Settings layout use the existing
PySide6 frontend and preserve existing launch, update, storage and access flows.
Original hand-drawn QtSvg path icons are embedded in `qt_navigation_icons.py`;
there are no external icon fonts, remote downloads or license dependencies.

## Implemented in this increment
- [x] Main drawer: vector icons, names and tooltips, keyboard focus and selected state
- [x] Settings: first actionable section is Updates, one original update handler and status
- [x] Shared Qt palette: tree/table selection, dialogs, progress, disabled and keyboard focus states
- [x] Existing light/dark palette and compact/comfortable preference settings retained
- [x] Synthetic offscreen Qt tests for navigation routing and updater placement

## Follow-up coverage required before claiming a full end-to-end UI overhaul
- [ ] Asset Library: check interaction with build membership filtering and sorted actions on real Windows UI
- [ ] Modding archive browsers: large data/long-path and wide-column checks
- [ ] Play & Restore: scaling and long status/diagnostic strings
- [ ] Model, animation and level editor toolbars: compact versus normal density
- [ ] File dialogs, export options and progress/cancel/error overlays: clipping and focus order
- [ ] 1280x720 and 1920x1080: 100%, 125%, 150%, 200% scaling
- [ ] Captured screenshots: light/dark, normal/compact, initial Settings position
- [ ] Windows compiled binaries and installer visual verification

A successful offscreen unit test is not evidence of all scaling or OpenGL
views. Only the Windows release workflow can verify built artifacts. Users must
check map/asset editor interactions on game installations and hardware.

No access-gated feature, update policy, source PAK, deployment target or stored
credentials are altered by the shared UI changes.
