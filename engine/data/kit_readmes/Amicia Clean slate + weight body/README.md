# Amicia Clean slate + weight body

**Purpose:** the starting point for your model. Import `clean_slate.glb`.

| Object | What it is |
|---|---|
| YOUR_armature | Amicia's skeleton. Weight your model to it and export it with your model. Never rename or move its bones. |
| REF_mannequin | The weighted body to model on. |
| REF_example_cloth_leather, REF_example_cloth_fabric, REF_example_skin | A finished example body, to look at. |

**Your model** replaces Amicia's whole body in every chapter: clothes, skin, head and hands. Only the game's eyes and
lashes stay. Two objects next to YOUR_armature, each with one material:
- **YOUR_cloth:** everything that is not skin: clothes, boots, belts (cloth shading).
- **YOUR_skin:** your head, hands and any other skin, with the eyelids weighted to the face bones (skin shading).

Untick chapter groups under Advanced to leave those chapters unchanged.

**Textures:** square images, 1024, 2048 or 4096 (the largest); each part can have its own size. In the Principled BSDF,
connect them to Base Color, Normal (through a Normal Map node), Roughness and Metallic; the exporter puts them in the file.

**Export:** use the settings on the tool's "How to export your model" page, then drop the files on the Model page.
