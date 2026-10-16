# Featured main-menu promotional controls (experimental)

The **GFX editor** exposes a **Featured promotional UI** section for
\`Libs/UI/main_menu.gfx\` in an editable Modding project only.

## Actions

- **Hide** title or whole promotion: hide immediately and repeatedly after video/featured-offer transitions through a menu-scoped ActionScript display-frame guard.
- **Remove / disable** title or whole promotion: set visibility false,
  disable referenced clips, blank text fields if present, and call
  \`removeMovieClip\` on dynamically attached clips. Static timeline
  clips that cannot be removed are still hidden; this does *not*
  physically delete static movie resource tags. The new persistent guard
  repeats the hide/disable on the menu's own invisible child clip, without
  replacing root onEnterFrame callbacks or registering a global timer.
- **Custom title text**: replace the runtime-populated featured title
  at the tail of \`setFeatured\` where a compatible text field exists.
  Bounded printable text is escaped as an ActionScript string.

These are **scripted runtime edits**, not texture edits and not static
placeholder-label replacement. Evolve's featured character/offer callback
(\`GetCharacterOfferInfo\`, \`setFeatured\`) may dynamically reassign
visibility/text after the injected function executes. Depending on game
build and scene state, an ActionScript edit can fail to take effect.
The Hide/Remove presets now repeat their visibility/disable changes on
each display frame after a video/offer transition, rather than only at the
end of setFeatured. Whole-root movie replacement or an unknown native
UI layer may still evade this mechanism. Test in-game, offline, across
multiple featured videos before distributing it.

## Safety / behavior

1. Only offered for \`Libs/UI/main_menu.gfx\`, not unrelated GFX.
2. Require the expected \`mcFeatureTitle\`, \`mcFeatureTry\`,
   \`mcFeatureBuy\` and \`setFeatured\` runtime symbols.
3. FFDec 26.3.0 exports the original ActionScript from a private
   editor-session copy, and the patcher identifies exactly one
   \`setFeatured\` function. Missing/ambiguous methods, unexpected early
   returns, invalid text, unsafe export paths, unsupported formats or
   output damage are **errors**, never silent fake successes.
4. FFDec recompiles the scripted change. Its result must be a valid,
   bounded GFX and retain the featured symbols before it may be staged.
5. The standard session **Undo staged edit** restores the previous
   GFX; **Apply to Mod Project** still requires explicit confirmation.
   Backups and offline-only mod packaging rules remain unchanged.
6. No copyrighted GFX/DDS content is checked into regression tests.

## Known limitation

The in-game title **GLACIAL BEHEMOTH** does not appear literally in the
inspected \`main_menu.gfx\` or \`UI_Data\` XML. UI_Data declares the
movie and callbacks, not the text source. Replacing bytes in a static
texture would not change it. This feature targets the likely ActionScript
update path; exact in-game behavior is unconfirmed without an offline
test of the matching Stage 2 build. Failure diagnostics offer editing the
\`setFeatured\` source manually using the full FFDec editor.

Persistent updates apply only to Hide/Remove; Custom text and Layout
overrides remain one-time operations. Hide Title does not disable TRY/BUY.

The first implementation does **not** register new custom promotions,
replace the 3D monster model, or modify the native feature-offer system.
