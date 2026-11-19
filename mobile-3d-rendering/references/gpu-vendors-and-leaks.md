# GPU families and driver memory leaks

## Tile-based GPUs (almost every phone)

Mali (Arm), Adreno (Qualcomm), PowerVR/IMG, Apple and Xclipse (Samsung RDNA) render the screen
in small tiles kept in on-chip memory. What follows from that:

- **Bandwidth is the scarce resource and the main source of heat.** Every full-screen pass
  (post effect, resolve, copy, render-target switch) writes the framebuffer out to RAM and reads
  it back. Avoid them on Low/Medium.
- **MSAA is cheap**: samples live on-chip and are resolved when the tile is written. A 4x MSAA
  forward pass usually costs much less than any post-AA pass.
- **Clear/discard render targets** at the start of a pass (engines do this when you don't
  reuse/load the previous contents). Loading the old contents costs a full read.
- **Overdraw**: opaque hidden-surface removal works (Mali Forward Pixel Kill, Adreno LRZ,
  PowerVR/Apple HSR) *only for opaque geometry without discard*. Alpha blending and alpha test
  (`discard`) defeat it, so large foliage cards and stacked particles are the classic GPU killers.
- **Vertex work is binned**: on Mali/PowerVR the geometry is processed before tiling and stored
  per frame. Huge triangle counts and vertex attributes cost memory bandwidth too, so keep vertex
  formats lean (half floats/compressed normals where the engine allows it).
- **Precision**: mediump/FP16 is twice as fast on many Mali/Adreno parts. Engines' mobile shaders
  use it by default; avoid forcing highp everywhere in custom shaders.

## Family notes

| Family | Seen in | Notes |
|---|---|---|
| Mali Bifrost (G71/G72/G76) | Galaxy S8/S9/**Note 9**, many mid-range 2017–2020 | Weak at heavy fragment shaders and bandwidth. Old GLES/Vulkan drivers. **Post-processing and render scaling leaked GL/native memory on G72 with Godot Compatibility** (see below). |
| Mali Valhall (G77–G720, Immortalis) | 2020+ Exynos/Dimensity/Tensor | Much better; Vulkan generally fine on recent drivers. |
| Adreno 5xx/6xx/7xx | Snapdragon | Strong ALU. FlexRender switches between binning and direct. Old 5xx Vulkan drivers are buggy, so prefer GLES there. |
| PowerVR | budget MediaTek/Unisoc | Very fill-limited; alpha test is especially expensive; keep Low tier modest. |
| Xclipse (RDNA) | Exynos 2200+ | Desktop-like; early drivers had issues, so test. |
| Apple | iPhone/iPad | Metal; excellent TBDR; memoryless render targets; thermals still matter. |

Detect the family at boot and use it to choose the default tier and a blocklist of features:
- Godot: `RenderingServer.get_video_adapter_name()` ("Mali-G72") and `get_video_adapter_vendor()`.
- Unity: `SystemInfo.graphicsDeviceName`.
- Bevy: the `RenderAdapterInfo` resource.
- Web: `WEBGL_debug_renderer_info`.
- adb: `dumpsys SurfaceFlinger | grep GLES`.

## Driver memory leaks: triage

Symptom: the game is killed after minutes ("app closed", no crash dialog). logcat shows
`lowmemorykiller`/`lmkd` killing the process. The engine's own memory counters stay **flat**.

1. **Reproduce and measure** with `device_probe.py -s 300`. Compare which rows grow:
   - `gl`/`egl`/`gfx` (GL mtrack, EGL mtrack, Graphics) growing: GPU driver allocations
     (render targets, textures, buffers, compiled programs).
   - `native` growing while the engine's counters are flat: driver CPU-side or a native plugin.
     If the engine's own static/heap memory grows too, it's your code.
   - A healthy game rises during the first 10–30 s (streaming, shader compiles), then goes flat
     within ±2–3 MB/min.
2. **Bisect with kill switches**: turn features off one at a time at runtime (post effects, render
   scale, MSAA, shadows, particles, sky, SubViewports, dynamic text) and re-run the probe. The
   feature whose removal flattens the slope is the trigger. It's usually a full-screen pass or a
   render target being reallocated.
3. **Classic triggers**:
   - Post-processing chains, render scaling and upscalers: intermediate targets. Mali-G72 /
     Godot 4.7 Compatibility leaked about 60–80 MB/s with glow, adjustments, or
     `scaling_3d/scale < 1`.
   - Re-creating render targets or viewports on resize, orientation change or settings change
     without freeing them.
   - Creating materials, meshes or textures every frame (each new material is a new driver
     program/state object). Changing font sizes or text per frame grows glyph atlases.
   - Readbacks/screenshots every frame. Particles that are restarted instead of reused.
   - three.js: removed objects that were never `dispose()`d.
4. **Workarounds**, ranked: don't use the feature on the affected family (blocklist) → use a cheaper
   equivalent baked into material shaders (fog, grading, glow halos) → switch the graphics API
   (GLES ↔ Vulkan) for that family → update the engine (driver workarounds land in engine
   releases).
5. **Write the rule down** in the project notes with the device, driver and evidence (MB/min), so
   nobody re-enables it.

## Other device-level gotchas

- **Display refresh**: 90/120 Hz phones run the compositor at that rate. A 60 fps game must ask
  for 60 (Android `Surface.setFrameRate`; engines do this when you cap fps, and Swappy handles
  pacing). Otherwise the cadence goes 2-1-2-1 and judders.
- **Resolution**: modern phones are 1080p to 1440p+ with DPR up to 3–4. Rendering at the native
  1440p is rarely worth the fill rate. A fixed 1080p-class swapchain is a common choice. Engine
  window/viewport stretch settings decide this without a post pass in many engines (test memory
  on Mali anyway).
- **Samsung Game Optimizing Service / game mode** can throttle or rescale games by package
  name. Results can differ from another package of the same app.
