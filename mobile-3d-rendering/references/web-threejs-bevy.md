# three.js / WebGL / WebGPU and Bevy (wgpu)

## three.js (mobile browsers and WebViews)

**Renderer setup**
```js
const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance',
                                           stencil: false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, isMobile ? 1.5 : 2)); // DPR 3 = 9x pixels
renderer.shadowMap.enabled = tier !== 'low';
renderer.shadowMap.type = THREE.PCFShadowMap;      // not PCFSoft/VSM on phones
renderer.shadowMap.autoUpdate = false;             // static sun: set needsUpdate = true when it moves
renderer.setAnimationLoop(frame);                  // not setInterval
```
- Pixel ratio is the biggest single fill-rate knob on the web. The browser composites the canvas
  anyway, so DPR 1.5 on a 3× phone looks fine.
- WebGL context loss happens on phones under memory pressure. Handle `webglcontextlost` /
  `webglcontextrestored`, or the game freezes black.

**Draw calls**
- Merge static geometry with `BufferGeometryUtils.mergeGeometries` (per material).
- Use `InstancedMesh` for repeats, and `BatchedMesh` for many different static meshes with one
  material *(r159+)*.
- Set `frustumCulled` true on chunks. Split huge merged meshes into spatial chunks so culling still
  works.
- Use `THREE.LOD` for distance swaps, plus `scene.fog` to hide them.

**Shader and program count**
- Every distinct material/defines combination is a WebGL program. Changing the number of lights
  or toggling `fog`/`shadow` at runtime recompiles. Keep the light count fixed.
- Warm up with `await renderer.compileAsync(scene, camera)` *(r158+)* behind the loading screen.
- `MeshLambertMaterial` / `MeshBasicMaterial` + baked AO are far cheaper per pixel than
  `MeshStandardMaterial`/`Physical` on low tiers.

**Textures and memory**
- KTX2 (Basis Universal) via `KTX2Loader` gives GPU-compressed textures (ASTC/ETC2 on phones).
  PNG/JPEG decode to full RGBA in VRAM.
- Meshopt or Draco geometry compression for downloads.
- **Dispose** what you remove: `geometry.dispose()`, `material.dispose()`, `texture.dispose()`,
  render targets. Without this, GPU memory only grows (the most common three.js leak).

**Post-processing**: use `pmndrs/postprocessing` (merges effects into one pass) or none on phones.
Half-resolution bloom only on High.

**Counters**
```js
const i = renderer.info;  // set i.autoReset = false and call i.reset() per frame if using multiple render calls
console.log(`[mem] calls=${i.render.calls} tris=${i.render.triangles} geos=${i.memory.geometries} tex=${i.memory.textures} programs=${i.programs.length}`);
```
- Rising `geometries`/`textures` over time means a leak (missing dispose).
- On Android, Chrome remote debugging (chrome://inspect) gives the Performance panel.
  `device_probe.py -p com.android.chrome` still gives the OS memory and thermals.
- GPU name: the `WEBGL_debug_renderer_info` extension. It may be masked, so fall back to a
  benchmark.

**WebGPU** (`WebGPURenderer`) *(check version and browser support)*: more efficient submission,
but Android browser support and drivers vary. Keep the WebGL2 path as default on phones until
the target devices are verified.

## Bevy (wgpu, Vulkan/GLES on Android)

APIs move between releases. Pair with `bevy-ecs` for current names, and check against the
`bevy` version in Cargo.toml.

- **Batching** is automatic for entities sharing the *same mesh handle and material handle*.
  Cloning `Handle<StandardMaterial>` keeps batches; `materials.add(...)` per entity breaks them.
  Use one material plus vertex colours, or a custom material with per-instance data.
- **MSAA**: the `Msaa` component on the camera (recent versions) / resource (older). Use
  `Msaa::Sample4` or `Off` with FXAA on High only.
- **Shadows**:
  - `DirectionalLightShadowMap { size: 1024 }` resource.
  - `CascadeShadowConfigBuilder { num_cascades: 1, maximum_distance: 50.0, .. }`.
  - `NotShadowCaster` on small props/ground, and `NotShadowReceiver` where it isn't needed.
- **HLOD**: the `VisibilityRange` component *(0.14+)* swaps near/far meshes by distance.
- **Unlit** `StandardMaterial { unlit: true }` for distant and background objects. Lower
  `perceptual_roughness` complexity doesn't matter, but **fewer lights does** (clustered forward).
- **Textures**: KTX2 + zstd (`ktx2`, `zstd` features), ASTC/ETC2 where `RenderDevice` features
  allow it.
- **Pipelines** compile asynchronously, so new materials pop in late rather than hitch. Spawn
  every material/mesh kind once during the loading state, off to the side of the camera.
- **Counters**: `FrameTimeDiagnosticsPlugin` + `LogDiagnosticsPlugin`, `RenderDiagnosticsPlugin`
  for GPU timings *(newer versions)*, and `bevy/trace_tracy` for Tracy. Log fps every few seconds
  and point `device_probe.py --log-regex` at it.
- **Android backend**: force Vulkan or GL via `WgpuSettings { backends: Some(Backends::VULKAN), .. }`
  when one driver misbehaves. Check the `RenderAdapterInfo` resource for the GPU name, for tier
  defaults.
