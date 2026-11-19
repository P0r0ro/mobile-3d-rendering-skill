# Unity (URP) on phones

URP is the mobile pipeline; Built-in is legacy and HDRP isn't for phones. Marked items depend on
the Unity version *(check version)*. Pair with `unity-build-pipeline` / `unity-build-size`.

## First things that bite

- **`Application.targetFrameRate` defaults to 30 on Android/iOS.** Set it to 60 explicitly (and
  `QualitySettings.vSyncCount = 0` on mobile, where vSync is ignored anyway).
- **Optimized Frame Pacing** (Player Settings → Android → Resolution and Presentation) uses Swappy
  for even frame cadence. Turn it on.
- **Graphics APIs**: Vulkan first with an OpenGLES3 fallback, or GLES3 only if Vulkan misbehaves on
  the oldest target Mali. Test both on the reference device.
- **Texture compression**: ASTC (Android/iOS). Set a per-platform Max Size override (1024 props,
  2048 atlases).

## URP Asset per quality level (Low / Medium / High)

| Setting | Mobile Medium | Notes |
|---|---|---|
| HDR | off (or on only for High) | HDR targets double bandwidth; bloom needs it |
| MSAA | 2x–4x | cheap on tilers |
| Render Scale | 1.0 (lower only after a memory soak on Mali) | below 1 adds an upscale pass |
| Depth Texture / Opaque Texture | off unless a shader needs them | each is a copy pass |
| Main Light shadows | 1024–2048, 1 cascade (2 on High), max distance 30–60 | |
| Soft shadows | off on Low/Medium | |
| Additional lights | Per Vertex or Disabled; shadows off | per-pixel lights are per-fragment loops |
| Reflection probes blending / box projection | off | |
| SRP Batcher | **on** | the main CPU-side win for many materials sharing a shader |
| Dynamic batching | off | usually slower than the SRP Batcher |
| LOD Cross Fade | off (dither = alpha test) | |
| Post-processing | off on Low/Medium; on High use cheap Bloom (no High-Quality Filtering) + LDR grading | each effect is a full-screen pass |

URP Renderer data:
- Rendering Path Forward (Forward+ if many lights on High).
- **Native RenderPass on** (keeps work on tile, Vulkan/Metal).
- **Depth Priming off** on tile GPUs.
- Remove unused Renderer Features.

Unity 6 *(check version)*: GPU Resident Drawer (BatchRendererGroup) needs Forward+ and compute,
and helps big instanced scenes on Vulkan devices. GPU occlusion culling builds on it. Measure
before enabling either on mid-range phones.

## Draw-call tools

- Static batching (mark static) for scenery that never moves.
- The SRP Batcher for many materials with the same shader variant.
- GPU instancing for repeated meshes. Note that the SRP Batcher takes precedence; use
  `Graphics.RenderMeshInstanced`/BRG for crowds of props.
- LOD Group with 3 levels plus Culled. Occlusion Culling bake (Umbra) for dense cities.
- Texture atlases and fewer materials. Use MaterialPropertyBlock for per-object colour (note: it
  breaks the SRP Batcher; prefer per-instance data or vertex colours).

## Stutter

- Shader variants:
  - Strip them: URP asset features off, `IPreprocessShaders`, "Strip Unused Variants".
  - Warm them up: `ShaderVariantCollection.WarmUp()` on GLES. On Vulkan/Metal use PSO warm-up
    (`GraphicsStateCollection`, Unity 6+ *(check version)*) during loading.
- Load with Addressables async. Spread `Instantiate` across frames. Use pools.
- GC: Incremental GC on, zero per-frame allocations in Update (strings, LINQ, boxing, `new` in hot
  paths).

## Thermals

Adaptive Performance package with the Android provider *(check version)* gives thermal warning
levels and performance-bottleneck hints, plus Scalers (LOD bias, shadow distance, resolution,
frame rate). Wire it to the same tier knobs as the settings menu.

## Measure

- Profiler attached to a Development Build (CPU, GPU module where supported, Rendering counters:
  SetPass calls, Draw Calls, Batches, Triangles).
- Frame Debugger for pass-by-pass draws.
- Memory Profiler package (look for duplicate textures and RenderTextures).
- Android GPU Inspector or Arm Performance Studio (Streamline) for GPU counters on Mali.
- Snapdragon Profiler for Adreno.
- `device_probe.py` for fps, memory slope and thermals over a soak. Log a line from code every
  5 s, for example with `FrameTimingManager` (enable "Frame Timing Stats"):

```csharp
// every 5 s in a debug build
Debug.Log($"[mem] fps={1f / Time.smoothDeltaTime:0} gfxMem={UnityEngine.Profiling.Profiler.GetAllocatedMemoryForGraphicsDriver() / 1048576}MB mono={UnityEngine.Profiling.Profiler.GetMonoUsedSizeLong() / 1048576}MB");
```

## Editor-side audit (sketch)

There's no runnable audit script for Unity in this skill. Check scenes with:
- the Frame Debugger;
- `UnityEditor.Rendering` analyzers / Project Auditor package *(check version)*;
- the Blender audit on source assets before import.
