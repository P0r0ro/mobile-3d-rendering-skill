# Looking good at a fixed cost

The rule: **move cost from every frame to bake time, and from full-screen passes into the
material shaders you already run.** A shader instruction on an opaque surface you already draw
costs far less than a separate pass over the screen.

## Lighting

| Instead of | Do | Cost |
|---|---|---|
| many dynamic lights | one directional sun + ambient (sky colour or gradient) + **baked** lighting | ~0 per frame |
| SSAO | AO baked into vertex colours (Blender: Bake → Vertex Colors, or "Dirty Vertex Colors") or lightmaps; contact darkening in the texture | 0 |
| real-time GI | lightmaps (Godot LightmapGI, Unity Progressive) + light probes for moving characters | bake time |
| omni light pools under lamps | additive, unshaded ground quad/decal with a radial gradient; switched by a global "night" param | 1 cheap transparent draw (or merge into the chunk) |
| shadows on everything | sun shadows on big casters near the player; **blob shadows** for crowds/cars on Medium; character shadows on High | |
| night lighting with real lights | emissive windows/neon in the building material, driven by one **global shader parameter** (e.g. `night` 0→1) | 0 extra draws |

## Atmosphere

- **Fog in the material shader** (distance plus height, coloured by the sky horizon) gives depth
  and mood. It hides LOD switches and lets you shorten the far plane and visibility ranges. It's
  the best price/quality trade on a phone. An engine's built-in fog also works if it's done
  in-shader (Godot's non-volumetric fog is). Keep volumetric fog for High only.
- **Sky**: a gradient sky-dome mesh (unshaded, vertex colours or a 1D gradient texture), or a
  low-res panorama. Avoid real-time procedural skies that re-render radiance every frame. Clouds
  can be a scrolling texture on the dome.
- **Sun/sky colour by time of day**: lerp a handful of colours (sun, ambient, fog, sky top and
  horizon) along a curve. Cheap, and it carries most of the "looks expensive" feel.

## "Post effects" without post

- **Bloom/glow**: additive billboard halos (a soft radial sprite) around lamps, neon and muzzle
  flashes. Emissive colours a bit above 1 with a halo read as bloom.
- **Colour grading / tone**: pick the palette in materials and lights. If you need a LUT, apply it
  in the material shader (one texture lookup on surfaces you already draw), or bake the grading
  into the textures.
- **Vignette / screen tint** for damage or drama: a full-screen *UI* rectangle with a gradient
  texture is one blended quad. Use it briefly, not permanently, since it's overdraw.
- **Depth of field / motion blur**: skip on phones. Use camera framing and fog instead.
- **Anti-aliasing**: MSAA 2–4x in the forward pass on tile GPUs. Avoid thin, high-contrast
  geometry and use mipmaps so textures don't shimmer.

## Materials and surfaces

- **Fresnel rim** (`pow(1 - dot(N, V), k)`) for silhouettes: cheap separation from the
  background.
- **Matcap** or a baked reflection cubemap for shiny cars, glass and water. No SSR or real-time
  probes.
- **Water**: two scrolling normal maps, fresnel to the sky colour, a depth tint from vertex
  colour, and foam from a texture. No refraction pass.
- **Normal maps** only on things seen up close (hero, nearby props), and only on Medium+.
  Distant objects get flat shading plus baked AO.
- **Detail textures**: a small tiling grime/noise texture multiplied in near the camera gives
  close-up detail without big unique textures.
- **Trim sheets and atlases**: one 2048 atlas per district or kit, so a whole chunk merges into
  one material = one draw.
- **Vertex colour regions** for characters and props (region codes chosen by shader params) mean
  many looks from one mesh and one material, with no per-NPC material copies.
- **Wind on trees/grass** in the vertex shader (sine on the vertex colour weight). There's no CPU
  cost.

## Characters and crowds (what players look at)

- The hero gets the triangles (5–15k), normal map on Medium+, real shadow on High, and full
  animation rate.
- Nearby NPCs: 2–5k triangles, the shared material with region colours, blob shadow, and
  animation at 30 Hz.
- Far NPCs: a reduced mesh/rig, animation at 10–15 Hz or paused off-screen, no attachments.
  Beyond ~80 m use impostors or nothing.
- Secondary motion (head look-at, breathing, hit reactions) sells life cheaply. Prioritise it over
  more triangles.

## Distance management (what is drawn at all)

1. Near (0–40 m): full detail, shadows.
2. Mid (40–120 m): merged chunk LOD, no small props, no shadows from small things.
3. Far (120 m–fog end): a single merged low-poly skyline per district, unshaded or vertex-lit.
4. Beyond: fog plus the sky dome. Draw nothing.

## Making High actually look higher

Spend the extra budget where the eye notices it, in this order:
1. Longer shadow distance and character shadows.
2. Longer detail ranges (less pop-in).
3. MSAA 4x.
4. Normal maps on more things.
5. Denser crowds and traffic.
6. More particles.
7. Half-resolution bloom / SSAO, but only on GPU families that passed a memory soak.
