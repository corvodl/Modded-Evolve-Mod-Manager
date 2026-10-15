# Model textures and packed animation previews

Open a model and use **Textures > Choose Model Textures…**. Select its material slot, browse the available imported texture folders (or add a folder with **Browse Folder…**), select a texture, and click **Use Selected Texture**. Repeat for other slots, then **Apply**. **Reset This Slot** restores the source material on Apply; Cancel discards pending changes.

Selections belong to the current model/equipment view for the editor session. The selected images are embedded in the current model's GLB, its assembled `Full_Character.glb`, and native animated GLB exports. Exported original CryEngine assets remain original; these choices are not game-asset edits. Independent batch conversion uses each source mesh's materials.

Material overrides now follow the selected CDF. Neighboring elite/defend variants no longer override a mesh's default material. Complete `.dds.0` mip chains are supported, and static diffuse tiling, offsets, and rotation are retained in preview and GLB exports (`KHR_texture_transform`). Animated shader effects are identified as static previews, not simulated.

For database-backed motion, open **Full Character** and choose a DBA or a missing CAF linked to a verified DBA in the animation browser. The bundled converter receives a temporary copy of the selected skin and the exact database. Its motion tracks are attached to the current rig only after joint names, hierarchy, and rest transforms match. Empty converter output and mismatched rigs are rejected. Source files are never modified.

CAF path annotations are preserved as metadata; source motion names are kept separate from gameplay aliases. A base-motion preview does not reproduce CryEngine's procedural aim or blend-graph behavior.
