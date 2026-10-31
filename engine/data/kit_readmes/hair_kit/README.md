# Hair kit

**Import `hair_kit.glb`.** It contains only:

| Object | What it is |
|---|---|
| YOUR_armature | The skeleton. Weight your hair to `head` and, for hair that swings, `hair1`-`hair5`. Export it with your hair. |
| REF_head_bald | The game's head without hair. |
| REF_hair_chapter_I, REF_hair_chapter_II, REF_hair_main_chapters, REF_hair_final_chapters | The game's hair in each chapter group, one object each. |

**Textures next to the kit** (to paint on or look at): `B01_hair.png` (the game's hair color and alpha) and
`B02_hair_normal.png`.

**Your hair:** one object, e.g. YOUR_hair, with one material; your own Color and Alpha images (or one RGBA image),
and a Normal image if you like. Your hair is used in all chapters (the game shares one hair texture between them).

**Export:** select YOUR_hair and YOUR_armature, then use the settings on the tool's "How to export your model" page.
Drop the file and the images on the Hair page.
