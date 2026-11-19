# Blender → engine for mobile

Audit every asset before it enters the game:
`blender -b --factory-startup --python scripts/blender_audit.py -- --tier mid <files>`
(tiers low/mid/high; `--json out.json` for CI). It reports tris (after modifiers), draw-call
estimate, materials, textures (raw and ASTC size), UVs, colour attributes, bone influences,
unapplied transforms, and n-gons, with a fix line for each flag.

## Budgets per asset (mid = Note 9 / Adreno 6xx class, ~150k tris and ~250 draws per frame)

| Asset | Low | Mid | High |
|---|---|---|---|
| Hero character | 3–5k | 5–15k | 15–40k |
| Crowd NPC (near LOD) | 1.5–3k | 2–5k | 5–10k |
| Car | 1.5–3k | 3–8k | 8–20k |
| Building (near) | 0.5–2k | 1–5k | 5–20k |
| Small prop | 50–300 | 100–800 | 300–2k |
| Materials per object | 1 | 1–2 | ≤4 |
| Texture size | 512–1024 | 1024 (2048 atlas) | 2048–4096 |
| Bones / influences | ≤40 / 2 | ≤75 / 4 | ≤150 / 4 |

The frame budget is spent on *what is in view*. A 5k-tri prop placed 200 times is 1M tris, so
it needs LODs and instancing, not just a low count.

## Modelling rules

- **Apply transforms** (Ctrl+A → All Transforms) so the engine sees scale 1. Non-uniform scale
  breaks batching/instancing in some engines and skews LOD and physics sizes.
- **Triangulate** before export (Triangulate modifier, keep normals) for predictable shading.
  Remove interior and hidden faces (bottoms of buildings, insides of cars).
- **Pivot** at the base centre for props (placement/LOD distance) and at the chunk centre for
  merged scenery.
- **Avoid micro triangles**: triangles under ~8 px on screen waste GPU quad shading. Decimate
  far LODs hard.
- **LODs**: name them `_LOD0/_LOD1/_LOD2` (Unity LOD Group convention; Godot can import them or
  generate its own). Use Decimate (Collapse) at ~50/25/10% and keep the silhouette. Far LODs drop
  small parts (wheels → part of body, window frames → texture).
- **Separate only what moves** (wheels, doors, weapons). Join static parts that share a material
  (Ctrl+J), since each mesh object is at least one draw.

## Materials, UVs, textures

- **One material per object**: pack textures into an atlas or trim sheet, or use vertex colours
  for flat-coloured kits (Kenney/Quaternius style), and colour per instance in the shader.
- **UV0** for textures. **UV1/UV2 non-overlapping** for lightmaps (Smart UV Project with margin,
  or Lightmap Pack). Godot's import can generate UV2 ("Light Baking: Static Lightmaps").
- **Bake AO into vertex colours** for cheap depth on untextured kits: Cycles → Bake type Ambient
  Occlusion → Target: Active Color Attribute. Export it as the colour attribute the shader
  multiplies in.
- **Textures**: power of two, sRGB for colour, linear for normal/roughness. Pack
  ORM (occlusion/roughness/metal) into one texture. Leave compression to the engine import
  (ASTC/ETC2). Downscale 4K sources before import, not at runtime.
- **Alpha**: prefer geometry cut-outs. If you must, use alpha *clip* with a tight mesh around the
  leaf/fence shape. Large blended planes cost overdraw on phones.

## Rigs and animation

- Limit to 4 influences (2 for crowd LODs): Weights → Limit Total, then Normalize All.
- Strip bones the camera never sees for crowd versions (fingers, twist, face) or use a reduced
  rig for far LODs.
- Bake actions, remove unused ones, and keep root motion consistent. Name loops so the engine
  sets loop mode (Godot: `-loop` / `_loop` suffix on import).
- Shape keys cost vertex work every frame when active. Keep them for the hero only.

## glTF export (Blender 4/5)

- Use glTF binary (`.glb`) with Apply Modifiers on, +Y Up on, and only the selected/visible
  collection.
- **Vertex colours** only export when asked: in Blender 5.x use
  `export_vertex_color="NAME", export_vertex_color_name="Col"` (or the matching UI option) and
  check the attribute name in the engine.
- Compression: leave Draco/meshopt off for Godot/Unity (they re-import into their own format).
  Use meshopt/Draco for three.js downloads.
- Headless batch export: `blender -b file.blend --python export.py`, so the pipeline is
  repeatable, versioned and runs in CI.

## Free CC0 sources that already fit mobile budgets

Kenney (kits: city, cars, furniture), Quaternius (characters + Universal Animation Library),
Poly Haven (textures/HDRIs; downscale them), Poly Pizza (low-poly). Keep the licence file next
to the assets.
