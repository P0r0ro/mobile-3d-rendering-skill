# Quality tiers, device detection and gameplay smoothness

## Tier table as data

Keep every knob in one table so the menu, the defaults, the governor and the debug flags all use
the same source:

| Knob | Low | Medium (reference phone) | High |
|---|---|---|---|
| target fps | 30 or 60 | 60 | 60 (or 90/120 option) |
| sun shadows | off (blob only) | on, 40–60 m, 1 cascade, 1–2k map | on, 90–150 m, 2 cascades, 2–4k |
| character shadows | blob | blob | real |
| detail / LOD range | short (or low LOD only) | medium | long |
| far chunk / fog end | near | medium | far |
| crowd / traffic density | 40% | 100% | 150% |
| animation rate far NPCs | 10 Hz | 15 Hz | 30 Hz |
| particles amount | 30% | 100% | 150% |
| MSAA | off/2x | 2x–4x | 4x |
| normal maps | off | hero + near | all |
| post effects | none | none (unless soaked safe on that family) | allowed per GPU family |

Rules:
- **Medium is defined by measurement**: the reference phone holds its target for a 10-minute soak
  with flat memory and thermal status ≤ 2.
- **High only adds** to Medium. Never make Medium worse to make High possible.
- Every knob also has a debug override (a flags file, a console variable or a URL param), so you
  can bisect on the device.
- Save the chosen tier per device (not in the cloud save; a phone and a tablet differ).

## Choosing the default

1. GPU family/model lists: known-weak → Low; reference class → Medium; flagship → High. Use the
   adapter name: "Mali-G72" → Medium/Low, "Adreno (TM) 740" → High, etc.
2. Or a first-run micro-benchmark: render the typical scene for 3–5 s at Medium after warm-up.
   If p95 frame time < 0.7 × budget, offer High; if > budget, drop to Low.
3. Device RAM: `ActivityManager.MemoryInfo.totalMem` / `SystemInfo.systemMemorySize` / Godot
   `OS.get_memory_info()["physical"]`. ≤ 3 GB → cap at Low/Medium (texture sizes, crowd counts).
4. Never auto-raise above the player's explicit choice. Auto-lowering for thermals must be
   temporary and visible in the settings.

## Adaptive governor (optional)

```
every 1 s:
  p95 = 95th percentile frame time over the last 3 s
  hot = thermal status >= 2 (or headroom < 0.3)
  if (p95 > budget * 1.05 or hot) and seconds_since_change > 3:
      step one knob down (order: shadow distance → crowd density → LOD range → particles → MSAA)
  elif p95 < budget * 0.75 and not hot and seconds_since_change > 20:
      step one knob back up (never past the player's tier)
```
Use hysteresis (fast down, slow up) so quality doesn't oscillate. Log every change so a soak run
shows what the governor did.

## Gameplay smoothness

- **Frame budget at 60 fps (16.6 ms)** on the reference phone, sustained. A typical split:

  | Work | Budget |
  |---|---|
  | game logic + AI | ~3 ms (staggered) |
  | physics | ~2 ms |
  | animation/skinning | ~2 ms |
  | render submission (CPU) | ~3–4 ms |
  | GPU | ≤ 11–12 ms |

  The leftover is headroom for heat and spikes.
- **Fixed-tick simulation plus render interpolation**: input feels the same at any fps and nothing
  judders on 90/120 Hz screens.
- **Input latency**: read input in the render-rate update, apply movement and the camera there (or
  interpolate). Avoid extra buffered frames: no triple buffering if it isn't needed for pacing,
  and keep the frame queue at 2.
- **Never block a gameplay frame**: loading, instancing, saving, shader compiles and pathfinding
  for many agents all need to be async or time-sliced.
- **Combat moments are the worst case**: explosions, many hit effects, ragdolls and crowds panicking
  at once. Benchmark *that* scene, not an empty street. Pool effects and cap simultaneous ones
  per tier.
- **Readability over fidelity**: clear silhouettes, strong rim/contrast on characters, and HUD
  legible on a 6" screen. These matter more to "feels good" than shadow resolution.
