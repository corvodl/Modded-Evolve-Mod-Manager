# Diagnose opaque or obstructing GFX root layers

The static FFDec preview of the supplied `Libs/UI/main_menu.gfx` in
beta.50 displayed the Evolve logo alongside an opaque red region.
That alone is **not evidence that every DDS failed**: the file has 11
first-frame root placements, and UI popups or other vector elements may
cover the static menu. No root depth has yet been verified as a popup.

## New controls

Next to **Canvas** and **Render referenced DDS in frame**, the GFX viewer
adds **Root layers**:

- **All layers**: original render behavior (default).
- **Hide selected layer**: removes *only* that root depth's placements
  from a temporary FFDec render copy.
- **Show selected layer only**: keeps only that root depth's
  placement-related tags in the temporary root display list.

Select a displayed first-frame root depth/character ID to compare
before and after. The dropdown shows export labels if available.
For the supplied main_menu.gfx sample the root first frame contains
11 depths, including exported labels `txt_timer`, `engagement_prompt`,
and `txt_label`. These names are not proof that any one is the red panel.

The filters operate on the root timeline only. Nested sprites, masks,
scripts, DDS sources, bitmap character definitions and FFDec imports
remain present; they are **not** individually hidden. Runtime-created
UI still requires executing the game's ActionScript/callback state.
This is a diagnostic isolation tool, not a permanent hide or delete
operation. A root-level Depth/Character selection is not necessarily
a single visible widget.

### Rendering integrity

- Filtering is applied to a **render-only** in-memory copy before
  external DDS bitmap materialization. The source `.gfx`, Asset Library
  and modding project bytes remain unchanged.
- FFDec still renders the original transforms, sprite nesting, masks,
  alpha and transparent PNG background for retained root layers.
- The toolbar's existing **Render referenced DDS in frame** option can
  be enabled or disabled independently to compare the textured and
  untextured versions. No automatic red-color keying is performed: real
  red UI artwork and the game logo must not be erased.
- Filtering is disabled in direct sprite-preview mode to avoid implying
  that root depths apply to sprite-local timelines.
- If the isolated red overlay is traced to a particular depth, use the
  existing visual layout editor and a separate confirmed edit session
  to make an intentional mod. Preview isolation by itself never stages
  a game modification.

## Validation

Dedicated Windows GFX tests verify the following with fabricated
GFX data: root-depth detection across timeline frames, hide/isolate,
unsupported-depth rejection, preservation of nested sprite definitions,
preservation of source bytes, Qt controls and a real FFDec pixel/alpha
render in which hiding a red first-frame shape produces transparency.

Further in-game confirmation is needed before naming the red rectangle
as any particular Evolve popup or claiming that runtime UI is complete.
