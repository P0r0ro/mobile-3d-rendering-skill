---
name: mobile-3d-rendering
description: >
  Makes 3D games look as good as the device allows while staying smooth: measure on the phone,
  find what limits the frame (draws, shadow pass, overdraw, bandwidth, CPU, thermals, driver
  memory leaks, shader stutter), apply the highest-payoff fix, verify, and scale visuals with
  quality tiers. Engine-neutral, with concrete settings for Godot 4, Unity URP, three.js/WebGL and
  Bevy, plus the Blender asset pipeline. Ships an Android soak probe (fps, jank, memory slope,
  thermal), a Blender asset audit and a Godot scene audit. Use for low or uneven fps on phones,
  crashes or kills after minutes of play, heat or battery drain, "make it look better without
  losing fps", graphics settings or tiers, draw-call or triangle budgets, LOD, shadows, Mali or
  Adreno quirks, or preparing Blender models for a mobile game. Not for general CPU/GC profiling
  (`performance-optimization`), shader maths (`shader-programming`) or exporting builds
  (`godot-export`, `unity-build-pipeline`).
---

# Mobile 3D rendering: best look that stays playable

The goal is **the best image the target device can hold at a stable frame rate for a long
session, without crashing**. "Stable" comes before "high": a locked 60 (or locked 30) feels
better than 50–90 jumping around, and a game that is killed after 8 minutes is a failure however
good it looks. Every rule here comes from one loop:

**measure on the device → name the limiter → apply the highest-payoff fix → re-measure (fps,
p95 frame time, memory slope, temperature) → keep it or revert → place it on a quality tier.**

Desktop numbers don't carry over to phones. Draw counts, triangle counts and memory sizes do;
milliseconds and fps don't. A change counts as done only once it has been checked on the device.

## 0. Before touching anything: collect the facts

Read the project's notes (README, CLAUDE.md, memory) for rules already learned the hard way, then
pin down:

| Fact | Where to get it |
|---|---|
| Engine + version + renderer | Godot: `project.godot` `rendering_method(.mobile)`; Unity: URP asset + Graphics API list; three.js: `WebGLRenderer`/`WebGPURenderer`; Bevy: `Cargo.toml` + wgpu backend |
| Reference device + GPU | `adb shell getprop ro.product.model`, `ro.hardware.egl`, engine adapter name (`RenderingServer.get_video_adapter_name()`, `SystemInfo.graphicsDeviceName`) |
| Target fps + display Hz | design decision; 60 on mid phones, 30 for heavy scenes on low-end |
| Existing quality tiers / kill switches | search for "quality", "gfx", "graphics", debug flags |
| How to build, install, launch, read logs | project README; otherwise see `references/` for your engine |

If the project has no on-device counter log, add one first (Godot snippet in
`references/godot.md`, Unity/three/Bevy equivalents in their references): one log line every
few seconds with fps, frame ms, draw calls, primitives and video memory. You can't fix what you
can't see on the device.

## 1. Baseline (three tools in `scripts/`)

```bash
S=~/.claude/skills/mobile-3d-rendering/scripts
# a) device soak: fps/jank from SurfaceFlinger, PSS/Native/GL/EGL memory slope, thermal status, CPU clock
python3 $S/device_probe.py -p <package> -s 120 --launch \
    --log-regex 'fps=(?P<fps>\d+).*draws=(?P<draws>\d+)'          # optional engine log line
#    -s 600 --interval 10 --csv soak.csv   for a thermal soak (10 min)
# b) scene audit (Godot): static estimate anywhere, real renderer counters under xvfb/GPU
godot --headless --path <proj> --script $S/godot_render_audit.gd -- res://main.tscn --tier mid
xvfb-run -a godot --path <proj> --rendering-driver opengl3 --render-thread safe \
    --script $S/godot_render_audit.gd -- res://main.tscn --wait 4 --frames 60
# c) asset audit (any engine that takes glTF/FBX/OBJ/.blend)
blender -b --factory-startup --python $S/blender_audit.py -- --tier mid assets/**/*.glb
```

Probe exit codes are 0 PASS, 1 WARN, 2 FAIL, so CI or a loop can gate on them. Write the baseline
down as a small table (fps median, p95 ms, jank %, draws, tris, memory slope per minute, thermal
status at 10 min) so every later change gets compared to it.

Read the outputs like this:
- **Process died, or memory rises steadily** (GL/EGL/Graphics/Native > ~3 MB/min after warm-up):
  a leak. If the engine's own memory counters stay flat while the OS numbers grow, it's the GPU
  driver. → `references/gpu-vendors-and-leaks.md`. Fix this before anything else.
- **fps below target**: find out what limits it (§2).
- **fps fine but jank % or worst-frame spikes**: stutter → `references/stutter-thermal-pacing.md`.
- **fps good at first and lower after minutes, or thermal status ≥ 2**: heat →
  `references/stutter-thermal-pacing.md`.
- **Audit WARN/ERROR lines**: each one names its fix. The heaviest-objects list says where to look.

## 2. Find the limiter (cheap experiments, on the device)

| Experiment (toggle at runtime via a debug flag; no rebuild) | If fps jumps | Limiter |
|---|---|---|
| Hide all 3D (keep HUD) | no change | CPU (game logic, physics, animation, UI) or the HUD itself |
| Hide HUD | big jump | canvas draws/overdraw (HUDs often are 40–60% of draws) |
| Shadows off | big jump | shadow pass (casters are gathered *all around* the camera up to max distance, not just on screen) |
| Shrink the render target (desktop or a safe device only; never ship scaling on Mali without a soak) | big jump | fill rate/bandwidth: overdraw, heavy fragment shaders, MSAA, post |
| Halve draw distance / LOD ranges | big jump | draw calls/vertices |
| Freeze animation updates / crowd AI | big jump | CPU skinning/animation/AI |

Then pick fixes from the catalogue for that limiter. **Change one thing at a time** and
re-measure.

## 3. Fix catalogue (ordered by typical payoff on phones)

**Draw calls / CPU submission** (budget on a Note 9 / Mali-G72 class phone: ~250 total including
shadow pass and HUD; flagship ~600+)
1. Merge static scenery per spatial chunk *per material* (one mesh per chunk, positioned at the
   chunk centre so distance-based culling/LOD works).
2. Instance repeated props (MultiMesh / GPU instancing / InstancedMesh / Bevy auto-batching), and
   split instances into spatial chunks so off-screen ones get culled.
3. One material per object: texture atlases, trim sheets, vertex colours for tints, per-instance
   params instead of material copies.
4. Visibility ranges / HLOD: detailed chunk near, merged low-poly chunk far, nothing beyond fog.
5. Shadow pass: shorter shadow distance, casting off for small props, roads, ground and far
   LODs, blob shadows for crowds, one cascade (or two) on mobile.
6. HUD: fewer nested containers/panels, one theme and atlas, hide what's off-screen, don't
   rebuild text every frame.

**Fill rate / bandwidth (tile GPUs: Mali, Adreno, PowerVR, Apple)**
1. No full-screen post passes on low/mid tiers (bloom, SSAO, DOF, colour grading, render
   scaling). Each costs a full read+write of the framebuffer, and some drivers leak memory with
   them.
2. Cut overdraw: opaque over alpha-blended; alpha-clip leaves foliage, but tight meshes; small,
   few, short-lived particles; no big transparent planes stacked up.
3. Cheap shaders: unshaded/vertex-lit for distant and small things; per-vertex fog/lighting;
   avoid `discard` on big surfaces (it disables early-Z / hidden surface removal).
4. MSAA 2x/4x is cheap on tilers (resolved on-chip); FXAA/TAA/SMAA are extra passes.
5. Texture bandwidth: GPU-compressed (ASTC/ETC2), mipmaps on everything in 3D, 1024 for most
   props, 2048 for atlases/hero.

**Vertices / skinning**
1. LODs at ~50/25/10% (generate in Blender or on import), triangle density matched to screen size.
2. Skinned characters: ≤4 influences (2 on low), fewer bones on crowd LODs, animation update
   rate lowered with distance (e.g. 60 Hz near, 15 Hz far, paused off-screen).

**Memory and stability**: see `references/gpu-vendors-and-leaks.md`. In short: soak-test every
rendering change for at least 60 s (10 min before release), keep GPU-family kill switches, and
never create materials, meshes or render targets per frame.

## 4. Look better without paying per frame

Most of what makes a mobile game look good is **baked or faked**:
- Bake AO and lighting into vertex colours or lightmaps (Blender, Godot LightmapGI, Unity
  Progressive Lightmapper). Use one directional light plus ambient and the bake. Fake lamp light
  pools with additive ground quads.
- Fog in the material shader (height + distance). It sets the mood *and* hides LOD switches and
  the far clip, so draw distance can be shorter.
- Emissive windows/neon driven by one global shader parameter (day/night), additive halo
  billboards instead of bloom.
- Gradient sky dome mesh or a static panorama instead of a procedural, real-time sky.
- Rim/fresnel and matcap in the material for depth. Use a baked reflection cubemap, not SSR.
- Colour grade by tuning materials and lights (or a LUT inside the material shader), not a post
  pass.
- Detail where the eye is: hero character, the street at your feet. Spend triangles and normal
  maps there, and almost nothing at 80 m.

Full list with engine recipes: `references/looks-on-a-budget.md`.

## 5. Quality tiers: the reference device runs Medium; High adds

- Define **Low / Medium / High** as data (a table of knobs): shadow distance and resolution,
  character shadows, LOD/visibility ranges, crowd and traffic density, particle amounts, MSAA,
  normal maps, extra effects. Medium = the reference phone at a locked target for a 10-min soak.
  Low = old/hot phones. High = everything Medium has plus extras for flagships and desktop.
- Default the tier from the GPU family and a quick first-run benchmark, save it per device, and
  let the player change it. Never raise the tier automatically above what the player chose.
- Every new visual feature gets (a) the tier it starts on and (b) a runtime kill switch, so you
  can bisect a device bug without rebuilding.
- Optional adaptive governor: if p95 frame time stays over budget for several seconds, or the
  thermal status rises, step one knob down (shadow distance → crowd → LOD ranges). Step it back
  up only after a long stretch of headroom (hysteresis), so the quality doesn't oscillate.

Details and device detection per engine: `references/quality-tiers-and-gameplay.md`.

## 6. Gameplay smoothness is part of rendering

- Budget the frame: at 60 fps aim for ≤ ~12 ms of real work (CPU and GPU) and leave the rest as
  thermal headroom. Phones that use 100% of the GPU throttle within minutes.
- Run physics and simulation at a fixed tick, render with interpolation, and keep camera follow
  in the render step. Stagger AI and crowd logic (e.g. 10 Hz, spread across frames).
- Never load, instantiate big scenes or compile shaders in the middle of gameplay. Prewarm during
  loading screens and time-slice spawning (`references/stutter-thermal-pacing.md`).
- Cap the frame rate (60, or 30 in menus). Uncapped rendering on a phone just makes heat.
- Logging every frame to logcat is slow; log every few seconds.

## 7. Verify and record

1. Re-run the same probe and audit as the baseline and show before/after numbers.
2. Take a device screenshot (`adb exec-out screencap -p > shot.png`) to check the look didn't
   regress.
3. Memory flat over ≥60 s (≥10 min before a release), and no process death.
4. Write any new hard-won rule into the project's notes (README/CLAUDE.md/memory) so the next
   session doesn't relearn it.

## References

| File | Read when |
|---|---|
| `references/godot.md` | Godot 4 project: renderer choice, settings, nodes, counters, Compatibility quirks |
| `references/unity-urp.md` | Unity URP: asset settings, batching, frame pacing, Adaptive Performance |
| `references/web-threejs-bevy.md` | three.js / WebGL / WebGPU or Bevy (wgpu) |
| `references/gpu-vendors-and-leaks.md` | Mali/Adreno/PowerVR/Apple behaviour, driver memory leaks, triage |
| `references/stutter-thermal-pacing.md` | hitches, shader warm-up, frame pacing, heat and battery |
| `references/looks-on-a-budget.md` | making it prettier at a fixed cost: bake, fake, fog, sky, characters |
| `references/blender-pipeline.md` | modelling and exporting assets for mobile (LOD, atlases, AO bake, glTF) |
| `references/quality-tiers-and-gameplay.md` | tier tables, device detection, adaptive governor, frame budget |
