# Godot 4 on phones

Version-sensitive items are marked *(check version)*. Check the docs for the installed version
before relying on them (`godot --version`).

## Renderer choice

| Renderer | API | Use on phones when |
|---|---|---|
| **Compatibility** (`gl_compatibility`) | GLES3 / WebGL2 | Widest device support, most predictable on older Mali/Adreno, also the web renderer. Fewer features (no SDFGI/VoxelGI/SSR/SSAO/volumetric fog). Good default for broad Android reach. |
| **Mobile** (`mobile`) | Vulkan | Newer phones with good Vulkan drivers; more features (more lights, decals, better shadows). Test on the oldest target; some Vulkan drivers on 2017–2019 phones are buggy. |
| Forward+ | Vulkan/D3D12/Metal | Desktop. Don't ship it on phones. |

Set `rendering/renderer/rendering_method.mobile` explicitly. The `.mobile` override is what
Android/iOS use.

## Project settings checklist (mobile overrides)

| Setting | Value | Why |
|---|---|---|
| `rendering/textures/vram_compression/import_etc2_astc` | `true` | otherwise Android gets uncompressed textures (4–8× VRAM/bandwidth) |
| `rendering/anti_aliasing/quality/msaa_3d` | 2x or 4x | cheap on tilers; avoid 8x |
| `rendering/scaling_3d/scale(.mobile)` | `1.0` unless proven safe | < 1.0 adds a full-screen resolve pass; on Mali-G72 (Note 9, Compatibility) it **leaked driver memory until the app was killed** |
| `rendering/lights_and_shadows/directional_shadow/size(.mobile)` | 2048 (1024 Low) | |
| `.../directional_shadow/soft_shadow_filter_quality(.mobile)` | Hard or Soft Very Low | PCF taps are per pixel |
| `.../positional_shadow/atlas_size(.mobile)` | 2048 or less, or 0 if no omni/spot shadows | |
| `rendering/driver/threads/thread_model` | Safe (1) | Separate (2) is *experimental*: it crashed `--script` runs (custom SceneTree) under 4.7.2 every time. Only enable it after a device soak shows a gain. |
| `physics/common/physics_interpolation` | on (4.3+) | removes judder when render fps ≠ physics tick; move cameras in `_process` |
| `application/run/max_fps` / `Engine.max_fps` | 60 | stop uncapped heat; 30 in menus if wanted |
| `rendering/mesh_lod/lod_change/threshold_pixels` | 1–4 | larger = more aggressive automatic mesh LOD |
| `rendering/occlusion_culling/use_occlusion_culling` | only with OccluderInstance3D baked in dense cities | CPU-side; measure it, it isn't free |

Environment (WorldEnvironment / Camera3D attributes): on Low/Medium mobile keep glow,
adjustments, SSAO, SSR, SSIL, SDFGI, volumetric fog and DOF **off**. Set sky `process_mode` to
Automatic/Quality (never Realtime), or use a background colour plus a sky-dome mesh. Gate post
effects behind `OS.has_feature("mobile")` *and* the tier. Note that the desktop run of an audit
sees the desktop branch.

## Nodes and patterns

- **Merge static geometry**: build one `ArrayMesh` per chunk per material (SurfaceTool
  `append_from()` / `commit()`), put the MeshInstance3D at the chunk centre, and give it
  `visibility_range_end`.
  - The visibility range is measured from the **node origin**, not the AABB. A merged mesh at the
    world origin is "near" everywhere.
- **HLOD with visibility ranges**: near node `range_end = 45`, far stand-in `range_begin = 45`,
  `range_end = 400`. Set `visibility_range_end_margin` (hysteresis) to a few metres. Fade modes
  dither through transparency, so keep `fade_mode = Disabled` on mobile and hide the switch with
  fog.
- **MultiMeshInstance3D**: one draw per surface for all instances, but culled as **one AABB**.
  Split per chunk. Use `visible_instance_count` to scale density per tier.
- **Shadows**:
  - Use `DirectionalLight3D.directional_shadow_mode = Orthogonal` (one pass) or PSSM 2 splits.
    Keep `directional_shadow_max_distance` short (40–60 m Medium, 90 m High).
  - `cast_shadow = OFF` on ground, roads, small props, far LODs, particles, Label3D/Sprite3D.
  - The shadow pass culls casters in a volume **around the camera**, not by what's on screen. On a
    city scene, 98 casters cost ~70 extra draws while only 23 objects were on screen (measured in
    4.7 Compatibility).
- **Omni/Spot lights**: unshadowed ones are cheapish, but each shadowed one re-renders its casters
  (cube omni = 6 views; dual paraboloid = 2). Compatibility has per-object light limits
  (`rendering/limits/opengl/max_lights_per_object`). Prefer baked light plus emissive and fake
  light pools.
- **Characters**: `Skeleton3D` skinning costs CPU in Compatibility *(check version)*.
  - For distant NPCs set `AnimationTree.callback_mode_process = MANUAL` and call `advance(dt)` at a
    lower rate.
  - Hide or reduce attachments (hair, hats) on far LODs. Use blob shadows (a shared quad with a
    radial gradient, `cast_shadow = OFF`) for crowds.
- **Label3D/Sprite3D** are transparent and each is a draw. Use few of them, show them only nearby,
  and set `no_depth_test` only where it's needed.
- **Particles**: GPUParticles3D needs transform feedback. On Compatibility, CPUParticles3D is often
  just as fast for small counts and more predictable. Scale `amount` per tier.
- **SubViewports** (minimaps, previews): `render_target_update_mode = WHEN_VISIBLE` or `ONCE`,
  never ALWAYS for static content.
- **Mesh LOD**: enable "Generate LODs" on import (default on). Set `lod_bias` lower on Low.
  Imported LODs are skipped if the mesh is made procedurally, so decimate those in Blender.
- **LightmapGI** for baked lighting (Compatibility support since 4.3/4.4 *(check version)*). The
  meshes need UV2 ("Light Baking: Static Lightmaps" on import).

## Counters (log these on the device)

```gdscript
# in an autoload, debug builds only, every 5 s
var t := 0.0
func _process(dt: float) -> void:
	t += dt
	if t < 5.0 or not OS.is_debug_build():
		return
	t = 0.0
	print("[mem] fps=%d proc=%.1fms draws=%d prims=%d objects=%d video=%.1fMB tex=%.1fMB static=%.1fMB" % [
		Engine.get_frames_per_second(),
		Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0,
		Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_TOTAL_OBJECTS_IN_FRAME),
		Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0,
		Performance.get_monitor(Performance.RENDER_TEXTURE_MEM_USED) / 1048576.0,
		Performance.get_monitor(Performance.MEMORY_STATIC) / 1048576.0])
```

Then `device_probe.py --log-regex 'fps=(?P<fps>\d+).*draws=(?P<draws>\d+)'`.

Split by pass: `get_viewport().get_render_info(Viewport.RENDER_INFO_TYPE_VISIBLE|SHADOW|CANVAS,
Viewport.RENDER_INFO_DRAW_CALLS_IN_FRAME)`.
- **Compatibility reports the shadow pass under VISIBLE**, and SHADOW stays 0. To see the shadow
  cost, toggle `shadow_enabled` and diff.
- `RENDER_TOTAL_DRAW_CALLS_IN_FRAME` includes canvas (HUD) draws.

GPU/CPU render time: `RenderingServer.viewport_set_measure_render_time(rid, true)` then
`viewport_get_measured_render_time_gpu(rid)`. It's only meaningful on the device.

## Runtime kill switches (bisect on the device without rebuilding)

Read a JSON file from `user://debug_flags.json` at boot. Write it with
`adb shell run-as <pkg> sh -c 'echo {\"no_shadows\":true} > files/debug_flags.json'` (debug
builds only). Wire one flag per feature: shadows, fog, sky, glow, crowd, hud, fx, msaa, and
detail/shadow distances as numbers. This turns "it leaks / it's slow" into a bisection in minutes.

## Audit script notes (`scripts/godot_render_audit.gd`)

- Autoloads load in `--script` mode (4.x). It runs the real scene, so game setup code runs too.
- If the project uses `thread_model=2`, pass `--render-thread safe`.
- For procedural worlds that need state set up first, copy the file into the project and call
  `RenderAudit.audit(get_tree().root, "mid")` and `await RenderAudit.sample(get_tree(), 60)` from a
  test scene after setup.
- The static estimate counts colour draws in the camera frustum, plus shadow casters within the
  sun's max distance all around. It tends to overestimate the shadow pass slightly (the engine
  culls casters to the light volume).

## Android export

- arm64-v8a only unless you need 32-bit. ETC2/ASTC on.
- For profiling, use a debug build plus the counters. Use a release build for final fps
  confirmation (GDScript debug overhead is real).
- Use `adb shell dumpsys meminfo <pkg>` for memory: **GL mtrack / EGL mtrack / Graphics** are the
  driver side, which Godot's own counters don't see.
