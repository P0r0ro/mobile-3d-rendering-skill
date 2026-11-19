# mobile-3d-rendering: an agent skill for smooth, good-looking 3D on phones

A [Claude Code](https://claude.com/claude-code) skill that helps a coding agent (or you) make 3D
games **look as good as the device allows while staying smooth, cool and crash-free**: measure on
the phone, find what limits the frame, apply the highest-payoff fix, verify, and scale visuals with
quality tiers.

The method is engine-neutral, with concrete settings and code for **Godot 4**, **Unity (URP)**,
**three.js / WebGL / WebGPU** and **Bevy (wgpu)**, plus the **Blender** asset pipeline. It comes
from real work shipping an open-world Godot game on a Samsung Galaxy Note 9 (Mali-G72), including
a GPU-driver memory leak that only showed up in `dumpsys meminfo`.

## What's inside

```
mobile-3d-rendering/
├── SKILL.md                         # the loop, limiter table, fix catalogue, tiers, gameplay smoothness
├── references/
│   ├── godot.md                     # renderer choice, project settings, nodes, counters, Compatibility quirks
│   ├── unity-urp.md                 # URP asset per tier, batching, frame pacing, Adaptive Performance
│   ├── web-threejs-bevy.md          # three.js (pixel ratio, KTX2, dispose, compileAsync) and Bevy
│   ├── gpu-vendors-and-leaks.md     # tile GPUs, Mali/Adreno/PowerVR/Apple notes, driver leak triage
│   ├── stutter-thermal-pacing.md    # shader warm-up, hitches, frame pacing, heat and battery
│   ├── looks-on-a-budget.md         # bake/fake: AO, fog, sky, glow halos, water, crowds, LOD bands
│   ├── blender-pipeline.md          # per-asset budgets, LODs, atlases, AO bake, rigs, glTF export
│   └── quality-tiers-and-gameplay.md# tier table, device detection, adaptive governor, frame budget
└── scripts/
    ├── device_probe.py              # Android soak probe (any engine)
    ├── blender_audit.py             # headless Blender asset audit
    └── godot_render_audit.gd        # Godot 4 scene audit (static + runtime)
```

## The tools

### `device_probe.py`: Android soak probe (any engine)

Reads the OS, not the engine, so it works for Godot, Unity, Unreal, Bevy and WebViews. Over a
soak it records:
- fps, p95 frame time, jank % and the worst frame, from SurfaceFlinger;
- PSS / Native / **GL mtrack / EGL mtrack / Graphics** memory, with a slope per minute, which
  catches driver leaks the engine's counters can't see;
- thermal status, temperatures and big-core clock %;
- whether the process died (with the lmkd or crash lines).

It can also parse your engine's own log line, and it exits 0 PASS / 1 WARN / 2 FAIL.

```bash
python3 mobile-3d-rendering/scripts/device_probe.py -p com.example.game -s 120 --launch \
    --log-regex 'fps=(?P<fps>\d+).*draws=(?P<draws>\d+)'
python3 mobile-3d-rendering/scripts/device_probe.py -p com.example.game -s 600 --interval 10 --csv soak.csv  # thermal soak
```

Needs Python 3.9+ and `adb` (found via `--adb`, `$ADB`, `~/Android/Sdk/platform-tools` or `PATH`).

### `blender_audit.py`: asset budgets

```bash
blender -b --factory-startup --python mobile-3d-rendering/scripts/blender_audit.py -- \
    --tier mid [--json report.json] assets/**/*.glb
```

Supports `.blend .glb .gltf .fbx .obj`. For each file it reports:
- triangles after modifiers, a draw-call estimate, materials, and texture memory (raw and ASTC);
- UVs, colour attributes and bone influences;
- unapplied transforms, n-gons, over-tessellation and alpha materials.

Each flag comes with a fix. Tiers are `low`, `mid` (Note 9 / Adreno 6xx class) and `high`.

### `godot_render_audit.gd`: Godot 4 scene audit

```bash
# static estimate (no GPU needed)
godot --headless --path <project> --script /abs/path/godot_render_audit.gd -- res://main.tscn --tier mid
# real renderer counters (GPU or xvfb)
xvfb-run -a godot --path <project> --rendering-driver opengl3 --render-thread safe \
    --script /abs/path/godot_render_audit.gd -- res://main.tscn --wait 4 --frames 60 --json out.json
```

What it reports:
- **Draw estimates**: on-screen colour draws, plus shadow-pass draws, counting casters all around
  the camera within shadow distance, the way the engine gathers them.
- **Costly content**: triangles, materials, transparency, particles, lights, Environment post
  effects, and risky project settings (texture compression, render scale, MSAA, thread model,
  physics interpolation).
- **Real counters** (with a GPU or xvfb): visible, shadow and **canvas/HUD** draws, primitives,
  video memory, and frame time.

For procedural worlds you can also `preload()` it inside the project and call `audit()` /
`sample()` from a test scene.

## Install

**Claude Code (user-level, all projects):**
```bash
git clone https://github.com/P0r0ro/mobile-3d-rendering-skill.git
cp -r mobile-3d-rendering-skill/mobile-3d-rendering ~/.claude/skills/
# or keep it updatable:
ln -s "$PWD/mobile-3d-rendering-skill/mobile-3d-rendering" ~/.claude/skills/mobile-3d-rendering
```

**Per project:** copy the `mobile-3d-rendering/` folder into `<project>/.claude/skills/`.

Restart Claude Code. The skill loads automatically for mobile rendering work (low fps on phones,
apps killed after minutes, heat, quality tiers, LOD/shadows/draw calls, Blender assets for
mobile), or you can call it with `/mobile-3d-rendering`. Other agents that read `SKILL.md`-style
skills can use the same folder. The scripts also work on their own without any agent.

## The core loop

1. **Collect facts**: engine, renderer, reference device and GPU, target fps, existing tiers.
2. **Baseline** with the three tools, and write the numbers down.
3. **Name the limiter** with cheap runtime toggles: HUD off, shadows off, 3D off, shorter ranges.
4. **Fix the biggest cost**:
   - draws: merging, instancing, atlases, HLOD, shadow casters, HUD;
   - fill rate and bandwidth: no post on low/mid, overdraw, MSAA instead of post-AA;
   - vertices and skinning;
   - memory.
5. **Re-measure on the device**: fps, p95, memory slope, thermals, plus a screenshot.
6. **Place it on a tier**: Medium = the reference phone over a 10-minute soak. High only adds.

## Hard-won rules it encodes

- Desktop draw, triangle and memory counts carry over to phones; desktop milliseconds and fps
  don't.
- Some Mali drivers **leak GPU memory with any post-processing path or render scaling < 1**. The
  engine's counters stay flat, and Android kills the app after minutes. Soak-test every rendering
  change for at least 60 s with `dumpsys meminfo`.
- Directional shadow casters are culled **around the camera**, not by the screen, so the shadow
  pass can cost several times the on-screen draws.
- HUDs can account for half the draw calls.
- Phones throttle within seconds to minutes. Budget about 70% of the frame and test a 10-minute
  soak.
- Every visual feature gets a tier and a runtime kill switch, so you can bisect on the device
  without rebuilding.

Settings that change between engine versions are marked *(check version)* in the references.
Corrections and additions for other engines and GPUs are welcome; open an issue or a PR.

## License

[MIT](LICENSE)
