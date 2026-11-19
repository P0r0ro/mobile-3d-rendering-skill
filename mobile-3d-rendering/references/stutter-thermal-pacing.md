# Stutter, frame pacing, heat and battery

## Telling them apart

| Probe shows | Problem |
|---|---|
| median fps on target, `worst frame` 100+ ms when something first appears | first-use hitch (shader/pipeline compile, texture upload, instancing) |
| periodic spikes every N seconds | GC, autosave, periodic sim tick, logging, streaming |
| fps on target but `jank %` > 2–5 with frames at 1.5–2× budget | pacing (cadence) or a constant just-over-budget load |
| fps fine for minutes, then falls; thermal status climbs; big-core clock % drops | thermal throttling |

## First-use hitches: prewarm everything during loading

- **Shaders/pipelines**: every material × mesh-format (skinned/static/instanced) × pass (colour,
  shadow, depth) × light setup is a separate compile. Show each one once behind the loading
  screen. A canvas-layer loading screen drawn *over* the 3D scene still lets the 3D draw (and
  compile) underneath. Spawn one of each prop/character/effect in front of the camera for 2–3
  frames, then remove them.
  - Godot: Vulkan renderers have ubershaders and pipeline precompilation (4.4+) *(check
    version)*. In Compatibility (GLES3), compiles happen on first draw, so the warm-up pass
    matters most there.
  - Unity: `ShaderVariantCollection.WarmUp` (GLES); PSO warm-up on Vulkan/Metal (Unity 6
    `GraphicsStateCollection`).
  - three.js: `renderer.compileAsync(scene, camera)`.
  - Bevy: spawn each material once during the loading state.
- **Particles/effects**: emit one burst off-screen during loading (Godot GPUParticles has a
  process material compile, and the first `emitting = true` mid-fight hitches).
- **Textures**: upload during loading. Avoid big `Image` → `ImageTexture` creation in gameplay.
- **Instancing big scenes**: build cities and chunks with a time-sliced builder (N ms per frame).
  Use background loading (`ResourceLoader.load_threaded_request`, Addressables async, three.js
  async loaders) for anything not needed this frame.
- **Audio**: first decode of long compressed audio can hitch. Preload it, and stream music.

## Periodic spikes

- Managed languages: no per-frame allocations (C# incremental GC; JS: reuse vectors/arrays).
  GDScript: avoid building big Dictionaries/Arrays per frame in hot paths.
- Spread periodic work: run 1/N of the agents each frame instead of all of them every N frames.
  Use a 10 Hz sim tick with jitter.
- Saves: serialize on a thread or in small slices, then do an atomic rename.
- Logging: printing every frame to logcat costs milliseconds on Android. Log at most every few
  seconds.

## Frame pacing (cadence)

- Cap to a divisor of the display rate: 60 on 60/120 Hz, 30 for heavy scenes. 45 on a 60 Hz panel
  can't pace evenly.
- Android pacing: Swappy (Unity "Optimized Frame Pacing"; Godot's Vulkan path in recent versions
  *(check version)*). Request the frame rate so 90/120 Hz panels drop to 60.
- Run game logic and physics at a fixed tick, and **interpolate rendering** (Godot 4.3+ physics
  interpolation; Unity Rigidbody interpolation; Bevy fixed timestep + transform interpolation).
  Move the camera in the render update.
- Leave headroom. A frame that uses 15.5 of 16.6 ms misses vsync whenever anything wobbles.

## Heat and battery (sustained performance)

Phones run at peak clocks for seconds to minutes, then throttle to their sustainable level,
often 50–70% of peak. Design for the **sustained** level:

- **Measure a 10-minute soak** with `device_probe.py -s 600 --interval 10`. Watch `tstatus`,
  `t_cpu` and `cpu_big_pct`. A Note 9 in a warm room reached thermal status 2 ("moderate") within
  30 s of launching a 3D game. Peak-clock fps is not what players get after five minutes.
- **Budget ~70% of the frame** (≈ 11–12 ms at 60 fps) on the reference device, so it holds after
  throttling.
- **Do less when nothing changes**: 30 fps in menus and inventory, and pause 3D rendering behind
  full-screen UI (hide the 3D viewport). Don't animate off-screen things. Godot
  `OS.low_processor_usage_mode` only helps when the screen is static.
- **Reduce work, not just pixels**: shadow distance, crowd and traffic counts, AI tick rate,
  animation update rate, particle counts. CPU heat is as real as GPU heat.
- **Read the OS thermal state**: Android `PowerManager.getCurrentThermalStatus()` (API 29) and
  `getThermalHeadroom()` (API 30). Unity Adaptive Performance exposes these; Godot needs a small
  Android plugin/JNI singleton. Feed them to the adaptive governor
  (`quality-tiers-and-gameplay.md`).
- **Battery**: the same levers. Also avoid keeping the GPU busy at 120 Hz in a game that only
  needs 60.
