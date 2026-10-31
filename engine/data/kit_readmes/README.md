# Kits

One folder per purpose. Each has its own short README. The kits are made from your own game files; they are never shared.

| Folder | Purpose |
|---|---|
| `Amicia Clean slate + weight body` | The clean slate to model on: the armature, the weighted body and an example. |
| `Amicia Parts` | The game's original outfit pieces: reference only. |
| `Amicia Seamless Textures` | Tiling skin, cloth, leather and metal textures made from your own game. |
| `hair_kit` | For a new hairstyle: the armature, a bald head, the game's hair, the hair textures. |
| `head_kit` | The eye color image (`B31_iris.png`) for the Eyes page, and the game's head for reference. |

**Names**
- **REF_ …:** reference only. Look at it; do not export it, do not edit it, do not use it in your model.
- **YOUR_ …:** what you edit or replace, or export with your model (the armature).

In the .glb files, the objects sit under two empties: "REFERENCE (do not export)" and "EDIT" (glTF files cannot store
collections). To keep the view clear, hide the REF_ objects in the Outliner (eye icon).

**Import:** File › Import › glTF 2.0, then choose the kit file.

**Export:** select only your YOUR_ objects (and YOUR_armature where the kit has one), then File › Export › glTF 2.0
with the settings on the tool's "How to export your model" page. Drop the file on the tool's Model or Hair page.
