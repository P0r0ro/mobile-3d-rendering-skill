extends SceneTree
## Godot 4.x render audit: what a 3D scene will cost on a phone, and what to change.
##
## Static (any machine, no GPU): walks the instanced scene, estimates draw calls/tris inside the
## active camera's frustum, flags expensive nodes, Environment effects and project settings.
##   godot --headless --path <project> --script <abs>/godot_render_audit.gd -- [res://x.tscn] [--tier mid]
##
## Runtime (real renderer numbers, needs a GPU or xvfb): also samples the engine's own counters
## (visible vs shadow draws, primitives, video memory, CPU/GPU frame time) after --wait seconds.
##   xvfb-run -a godot --path <project> --rendering-driver opengl3 --script <abs>/godot_render_audit.gd \
##       -- res://x.tscn --frames 120 --wait 3 [--json out.json]
##
## In-game (procedural worlds that only exist after gameplay setup): copy this file into the
## project, then from any test scene or debug key:
##   const RenderAudit := preload("res://tools/godot_render_audit.gd")
##   print(RenderAudit.format(RenderAudit.audit(get_tree().root, "mid")))
##   print(RenderAudit.format_runtime(await RenderAudit.sample(get_tree(), 60)))
## Scene with no arg = the project's main scene. Exit code 1 if any ERROR flag.

const TIERS: Dictionary = {
	"low": {"draws": 150, "tris": 80000, "shadow_casters": 60, "lights_shadow": 0, "particles": 2000, "transparent": 30},
	"mid": {"draws": 250, "tris": 150000, "shadow_casters": 120, "lights_shadow": 1, "particles": 4000, "transparent": 60},
	"high": {"draws": 600, "tris": 500000, "shadow_casters": 400, "lights_shadow": 4, "particles": 20000, "transparent": 150},
}


func _initialize() -> void:
	var a: PackedStringArray = OS.get_cmdline_user_args()
	var scene_path: String = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	var tier: String = "mid"
	var frames: int = 0
	var wait: float = 2.0
	var json_out: String = ""
	var i: int = 0
	while i < a.size():
		var s: String = a[i]
		if s == "--tier" and i + 1 < a.size():
			i += 1
			tier = a[i]
		elif s == "--frames" and i + 1 < a.size():
			i += 1
			frames = int(a[i])
		elif s == "--wait" and i + 1 < a.size():
			i += 1
			wait = float(a[i])
		elif s == "--json" and i + 1 < a.size():
			i += 1
			json_out = a[i]
		elif not s.begins_with("--"):
			scene_path = s
		i += 1
	if not TIERS.has(tier):
		printerr("tier must be low|mid|high")
		quit(2)
		return
	if int(ProjectSettings.get_setting("rendering/driver/threads/thread_model", 1)) == 2 and "--render-thread" not in OS.get_cmdline_args():
		print("[audit] project uses the experimental separate render thread; with --script it can crash the engine. Re-run with --render-thread safe if it does.")
	var ps: PackedScene = load(scene_path) as PackedScene
	if ps == null:
		printerr("cannot load scene: ", scene_path)
		quit(2)
		return
	var inst: Node = ps.instantiate()
	root.add_child(inst)
	for _f: int in 3:
		await process_frame
	if wait > 0.0:
		await create_timer(wait).timeout
	var rep: Dictionary = audit(root, tier)
	rep["scene"] = scene_path
	print(format(rep))
	if frames > 0:
		if DisplayServer.get_name() == "headless":
			print("[runtime] skipped: headless has no renderer (use xvfb-run + --rendering-driver opengl3, or a device)")
		else:
			var rt: Dictionary = await sample(self, frames)
			rep["runtime"] = rt
			print(format_runtime(rt, tier))
	if json_out != "":
		var f: FileAccess = FileAccess.open(json_out, FileAccess.WRITE)
		if f:
			f.store_string(JSON.stringify(rep, " "))
	inst.queue_free()
	await process_frame
	var bad: bool = false
	for fl: Dictionary in rep.flags:
		if str(fl.level) == "ERROR":
			bad = true
	quit(1 if bad else 0)


# --------------------------------------------------------------------------- static audit

static func audit(top: Node, tier: String = "mid") -> Dictionary:
	var B: Dictionary = TIERS.get(tier, TIERS.mid)
	var vp: Viewport = top.get_viewport()
	var cam: Camera3D = vp.get_camera_3d() if vp else null
	var planes: Array[Plane] = []
	if cam:
		planes = cam.get_frustum()
	var r: Dictionary = {
		"tier": tier, "camera": str(cam.get_path()) if cam else "", "nodes": 0,
		"geom": 0, "geom_in_view": 0, "draws_est": 0, "shadow_draws_est": 0, "tris_in_view": 0,
		"tris_total": 0, "materials": 0, "shader_materials": 0, "transparent_in_view": 0,
		"shadow_casters": 0, "skinned": 0, "multimesh_instances": 0, "particles": 0,
		"lights": 0, "lights_shadow": 0, "label3d_sprite3d": 0, "no_vis_range": 0, "flags": [],
		"heaviest": [],
	}
	var flags: Array = r.flags
	var mats: Dictionary = {}
	var heavy: Array = []
	var sun_shadow_dist: float = 0.0
	for dn: Node in top.find_children("*", "DirectionalLight3D", true, false):
		var d3: DirectionalLight3D = dn
		if d3.is_visible_in_tree() and d3.shadow_enabled:
			sun_shadow_dist = maxf(sun_shadow_dist, d3.directional_shadow_max_distance)
	var stack: Array[Node] = [top]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		r.nodes += 1
		for c: Node in n.get_children():
			stack.push_back(c)
		if n is Node3D and not (n as Node3D).is_visible_in_tree():
			continue
		if n is DirectionalLight3D:
			var dl: DirectionalLight3D = n
			r.lights += 1
			if dl.shadow_enabled:
				if dl.directional_shadow_mode != DirectionalLight3D.SHADOW_ORTHOGONAL:
					flags.append(_f("INFO", "%s: %s shadow splits" % [dl.name, "4" if dl.directional_shadow_mode == DirectionalLight3D.SHADOW_PARALLEL_4_SPLITS else "2"],
						"each split re-draws casters; on mobile use Orthogonal or 2 splits with a short max distance"))
		elif n is OmniLight3D or n is SpotLight3D:
			var l: Light3D = n
			r.lights += 1
			if l.shadow_enabled:
				r.lights_shadow += 1
		elif n is WorldEnvironment:
			_env_flags((n as WorldEnvironment).environment, flags)
		elif n is Camera3D and (n as Camera3D).environment:
			_env_flags((n as Camera3D).environment, flags)
		elif n is VoxelGI:
			flags.append(_f("WARN", "VoxelGI %s" % n.name, "not supported in Compatibility, heavy on Mobile; bake LightmapGI or vertex AO instead"))
		elif n is ReflectionProbe and (n as ReflectionProbe).update_mode == ReflectionProbe.UPDATE_ALWAYS:
			flags.append(_f("ERROR", "ReflectionProbe %s updates every frame" % n.name, "set update_mode = Once"))
		elif n is GPUParticles3D or n is CPUParticles3D:
			var amt: int = int(n.get("amount"))
			r.particles += amt
		if n is Label3D or n is Sprite3D:
			r.label3d_sprite3d += 1
		if not (n is GeometryInstance3D):
			continue
		var g: GeometryInstance3D = n
		r.geom += 1
		var aabb: AABB = g.get_global_transform() * g.get_aabb()
		# visibility ranges are measured from the node origin (not the AABB centre)
		var dist: float = cam.global_position.distance_to(g.global_position) if cam else 0.0
		var in_range: bool = g.visibility_range_end <= 0.0 or (dist >= g.visibility_range_begin and dist <= g.visibility_range_end + g.visibility_range_end_margin)
		var in_view: bool = in_range and (planes.is_empty() or _aabb_in_frustum(aabb, planes))
		var surfaces: int = 1
		var tris: int = 0
		var transparent: bool = false
		var mesh: Mesh = null
		var mult: int = 1
		if g is MeshInstance3D:
			var mi: MeshInstance3D = g
			mesh = mi.mesh
			if mi.skin != null or mi.skeleton != NodePath(""):
				if mi.get_node_or_null(mi.skeleton) is Skeleton3D:
					r.skinned += 1
		elif g is MultiMeshInstance3D:
			var mm: MultiMesh = (g as MultiMeshInstance3D).multimesh
			if mm:
				mesh = mm.mesh
				mult = mm.visible_instance_count if mm.visible_instance_count >= 0 else mm.instance_count
				r.multimesh_instances += mult
		if mesh:
			surfaces = mesh.get_surface_count()
			tris = _mesh_tris(mesh) * mult
			for s: int in surfaces:
				var m: Material = g.material_override
				if m == null and g is MeshInstance3D:
					m = (g as MeshInstance3D).get_active_material(s)
				if m == null:
					m = mesh.surface_get_material(s)
				if m:
					mats[m.get_instance_id()] = m
					if m is BaseMaterial3D and (m as BaseMaterial3D).transparency != BaseMaterial3D.TRANSPARENCY_DISABLED:
						transparent = true
		if g is Label3D or g is Sprite3D or g is GPUParticles3D or g is CPUParticles3D:
			transparent = true
		r.tris_total += tris
		if g.visibility_range_end <= 0.0 and tris > 3000 and mult == 1:
			r.no_vis_range += 1
		# directional shadow casters are gathered all around the camera (not only on screen),
		# up to the shadow max distance: off-screen props behind you still cost a draw each.
		if in_range and sun_shadow_dist > 0.0 and g.cast_shadow != GeometryInstance3D.SHADOW_CASTING_SETTING_OFF and not transparent:
			var cd: float = cam.global_position.distance_to(aabb.get_center()) if cam else 0.0
			if cd <= sun_shadow_dist + aabb.get_longest_axis_size() * 0.5:
				r.shadow_casters += 1
				r.shadow_draws_est += surfaces
		if not in_view:
			continue
		r.geom_in_view += 1
		r.draws_est += surfaces
		r.tris_in_view += tris
		if transparent:
			r.transparent_in_view += 1
		heavy.append({"path": str(g.get_path()), "tris": tris, "surfaces": surfaces, "dist": snappedf(dist, 0.1)})
	for m: Material in mats.values():
		if m is ShaderMaterial:
			r.shader_materials += 1
	r.materials = mats.size()
	heavy.sort_custom(func(x: Dictionary, y: Dictionary) -> bool: return int(x.tris) > int(y.tris))
	r.heaviest = heavy.slice(0, 10)
	var total_draws: int = int(r.draws_est) + int(r.shadow_draws_est)
	if total_draws > int(B.draws):
		flags.append(_f("ERROR" if total_draws > int(B.draws) * 3 / 2 else "WARN",
			"~%d draws in view (%d colour + %d shadow) > %d" % [total_draws, r.draws_est, r.shadow_draws_est, B.draws],
			"merge static meshes per area/chunk by material, MultiMesh repeated props, atlas materials, shorten visibility ranges, cast_shadow=OFF on small/far props"))
	if int(r.tris_in_view) > int(B.tris):
		flags.append(_f("WARN", "%dk tris in view > %dk" % [int(r.tris_in_view) / 1000, int(B.tris) / 1000],
			"mesh LOD (import 'Generate LODs'), visibility_range for HLOD swaps, decimate far assets"))
	if int(r.shadow_casters) > int(B.shadow_casters) or int(r.shadow_draws_est) > int(r.draws_est):
		flags.append(_f("WARN", "%d shadow casters within %.0f m (shadow pass ~%d draws vs %d colour draws)" % [r.shadow_casters, sun_shadow_dist, r.shadow_draws_est, r.draws_est],
			"casters are culled around the camera, not by the screen: shorter directional_shadow_max_distance, cast_shadow OFF on small props/far LODs/ground/roads, merge casters per chunk, blob shadows for crowds"))
	if int(r.lights_shadow) > int(B.lights_shadow):
		flags.append(_f("WARN", "%d shadowed omni/spot lights" % r.lights_shadow,
			"each one re-renders its casters (up to 6x for omni); bake them, or allow shadows only on High"))
	if int(r.transparent_in_view) > int(B.transparent):
		flags.append(_f("WARN", "%d transparent objects in view" % r.transparent_in_view,
			"overdraw is the #1 GPU cost on tile GPUs: alpha scissor/opaque where possible, fewer/smaller particles, billboards with tight meshes"))
	if int(r.particles) > int(B.particles):
		flags.append(_f("WARN", "%d particles" % r.particles, "cut amount, shorter lifetime, smaller quads; scale amount by quality tier"))
	if int(r.no_vis_range) > 20:
		flags.append(_f("INFO", "%d meshes >3k tris with no visibility_range" % r.no_vis_range,
			"give far scenery visibility_range_end (and a low-detail stand-in with range_begin) so it stops costing at distance"))
	if int(r.materials) > 60:
		flags.append(_f("INFO", "%d unique materials" % r.materials, "every unique material/shader variant is a state change and a first-use compile; share materials, use per-instance params/vertex colour"))
	_project_flags(flags)
	return r


static func _mesh_tris(mesh: Mesh) -> int:
	var t: int = 0
	if mesh is ArrayMesh:
		var am: ArrayMesh = mesh
		for s: int in am.get_surface_count():
			var idx: int = am.surface_get_array_index_len(s)
			t += (idx if idx > 0 else am.surface_get_array_len(s)) / 3
		return t
	for s: int in mesh.get_surface_count():
		var arr: Array = mesh.surface_get_arrays(s)
		var ind: Variant = arr[Mesh.ARRAY_INDEX]
		if ind is PackedInt32Array and (ind as PackedInt32Array).size() > 0:
			t += (ind as PackedInt32Array).size() / 3
		elif arr[Mesh.ARRAY_VERTEX] is PackedVector3Array:
			t += (arr[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3
	return t


static func _aabb_in_frustum(b: AABB, planes: Array[Plane]) -> bool:
	for p: Plane in planes:
		# positive vertex test (Godot camera frustum planes point outward)
		var v: Vector3 = b.position
		if p.normal.x < 0.0:
			v.x += b.size.x
		if p.normal.y < 0.0:
			v.y += b.size.y
		if p.normal.z < 0.0:
			v.z += b.size.z
		if p.distance_to(v) > 0.0:
			return false
	return true


static func _f(level: String, what: String, fix: String) -> Dictionary:
	return {"level": level, "what": what, "fix": fix}


static func _env_flags(env: Environment, flags: Array) -> void:
	if env == null:
		return
	var mobile_post: Array[String] = []
	if env.glow_enabled:
		mobile_post.append("glow")
	if env.adjustment_enabled:
		mobile_post.append("adjustments")
	if env.ssao_enabled:
		mobile_post.append("SSAO")
	if env.ssr_enabled:
		mobile_post.append("SSR")
	if env.ssil_enabled:
		mobile_post.append("SSIL")
	if env.sdfgi_enabled:
		mobile_post.append("SDFGI")
	if env.volumetric_fog_enabled:
		mobile_post.append("volumetric fog")
	if not mobile_post.is_empty():
		flags.append(_f("WARN", "Environment post effects: %s" % ", ".join(mobile_post),
			"full-screen passes cost bandwidth on tile GPUs, and some Mali drivers leak memory with them; gate behind the High tier and soak-test memory on device"))
	if env.background_mode == Environment.BG_SKY and env.sky and env.sky.process_mode == Sky.PROCESS_MODE_REALTIME:
		flags.append(_f("WARN", "Sky process_mode Realtime", "use Automatic/Quality or bake; realtime sky re-renders radiance each frame"))


static func _ps(key: String, def: Variant) -> Variant:
	if ProjectSettings.has_setting(key + ".mobile"):
		return ProjectSettings.get_setting(key + ".mobile")
	return ProjectSettings.get_setting(key, def)


static func _project_flags(flags: Array) -> void:
	var method: String = str(_ps("rendering/renderer/rendering_method", "forward_plus"))
	if method == "forward_plus":
		flags.append(_f("WARN", "mobile renderer is Forward+", "set rendering_method.mobile to 'mobile' (Vulkan) or 'gl_compatibility' (GLES3; most stable on older Mali/Adreno)"))
	var sc: float = float(_ps("rendering/scaling_3d/scale", 1.0))
	if sc < 1.0:
		flags.append(_f("WARN", "scaling_3d scale %.2f on mobile" % sc, "render scaling adds a full-screen pass; it leaked driver memory on Mali-G72 (Note 9). Soak-test meminfo on the target before keeping it"))
	if not bool(ProjectSettings.get_setting("rendering/textures/vram_compression/import_etc2_astc", false)):
		flags.append(_f("ERROR", "ETC2/ASTC texture import is off", "enable rendering/textures/vram_compression/import_etc2_astc, or Android textures stay uncompressed (4-8x VRAM)"))
	var msaa: int = int(_ps("rendering/anti_aliasing/quality/msaa_3d", 0))
	if msaa > 2:
		flags.append(_f("INFO", "MSAA %dx" % [1 << msaa], "4x is cheap on tile GPUs (resolved on-chip), 8x is not; keep at 2x/4x"))
	if int(_ps("rendering/lights_and_shadows/directional_shadow/size", 4096)) > 2048:
		flags.append(_f("INFO", "directional shadow map > 2048 on mobile", "2048 (or 1024 on Low) with a short max distance looks the same on a phone screen"))
	if int(_ps("rendering/lights_and_shadows/directional_shadow/soft_shadow_filter_quality", 2)) > 2:
		flags.append(_f("INFO", "soft shadow filter quality high", "use Soft Low / Hard on mobile"))
	if not bool(ProjectSettings.get_setting("physics/common/physics_interpolation", false)) and int(ProjectSettings.get_setting("physics/common/physics_ticks_per_second", 60)) < 90:
		flags.append(_f("INFO", "physics interpolation off", "on 90/120 Hz displays or when fps != physics rate, moving bodies judder; enable it (4.3+) and set the camera to follow in _process"))


static func format(r: Dictionary) -> String:
	var o: PackedStringArray = []
	o.append("=== render audit %s (tier %s, camera %s)" % [r.get("scene", ""), r.tier, r.camera if str(r.camera) != "" else "none: counting everything"])
	o.append("in view: %d geom  ~%d colour draws + ~%d shadow-pass draws  %dk tris  (scene total %d geom, %dk tris)" % [r.geom_in_view, r.draws_est, r.shadow_draws_est, int(r.tris_in_view) / 1000, r.geom, int(r.tris_total) / 1000])
	o.append("materials %d (%d shader)  transparent %d  shadow casters (all around) %d  skinned %d  multimesh inst %d  particles %d  lights %d (%d shadowed omni/spot)  label3d/sprite3d %d" % [r.materials, r.shader_materials, r.transparent_in_view, r.shadow_casters, r.skinned, r.multimesh_instances, r.particles, r.lights, r.lights_shadow, r.label3d_sprite3d])
	o.append("heaviest in view:")
	for h: Dictionary in r.heaviest:
		o.append("  %7d tris %2d surf %6.1f m  %s" % [h.tris, h.surfaces, h.dist, h.path])
	var order: Dictionary = {"ERROR": 0, "WARN": 1, "INFO": 2}
	var fl: Array = (r.flags as Array).duplicate()
	fl.sort_custom(func(x: Dictionary, y: Dictionary) -> bool: return int(order[x.level]) < int(order[y.level]))
	for f: Dictionary in fl:
		o.append("[%s] %s\n        -> %s" % [f.level, f.what, f.fix])
	return "\n".join(o)


# --------------------------------------------------------------------------- runtime sampling

static func sample(tree: SceneTree, frames: int = 60) -> Dictionary:
	var vp: Viewport = tree.root
	var rid: RID = vp.get_viewport_rid()
	RenderingServer.viewport_set_measure_render_time(rid, true)
	for _w: int in 5:
		await tree.process_frame
	var acc: Dictionary = {"draws": 0.0, "prims": 0.0, "objects": 0.0, "shadow_draws": 0.0, "visible_draws": 0.0, "canvas_draws": 0.0,
		"cpu_ms": 0.0, "gpu_ms": 0.0, "process_ms": 0.0}
	var worst: float = 0.0
	var last: int = Time.get_ticks_usec()
	for _i: int in frames:
		await tree.process_frame
		var now: int = Time.get_ticks_usec()
		worst = maxf(worst, (now - last) / 1000.0)
		last = now
		acc.draws += Performance.get_monitor(Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME)
		acc.prims += Performance.get_monitor(Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME)
		acc.objects += Performance.get_monitor(Performance.RENDER_TOTAL_OBJECTS_IN_FRAME)
		acc.visible_draws += vp.get_render_info(Viewport.RENDER_INFO_TYPE_VISIBLE, Viewport.RENDER_INFO_DRAW_CALLS_IN_FRAME)
		acc.shadow_draws += vp.get_render_info(Viewport.RENDER_INFO_TYPE_SHADOW, Viewport.RENDER_INFO_DRAW_CALLS_IN_FRAME)
		acc.canvas_draws += vp.get_render_info(Viewport.RENDER_INFO_TYPE_CANVAS, Viewport.RENDER_INFO_DRAW_CALLS_IN_FRAME)
		acc.cpu_ms += RenderingServer.viewport_get_measured_render_time_cpu(rid) + RenderingServer.get_frame_setup_time_cpu()
		acc.gpu_ms += RenderingServer.viewport_get_measured_render_time_gpu(rid)
		acc.process_ms += Performance.get_monitor(Performance.TIME_PROCESS) * 1000.0
	var out: Dictionary = {}
	for k: String in acc:
		out[k] = snappedf(float(acc[k]) / frames, 0.01)
	out["fps"] = Performance.get_monitor(Performance.TIME_FPS)
	out["worst_frame_ms"] = snappedf(worst, 0.1)
	out["video_mem_mb"] = snappedf(Performance.get_monitor(Performance.RENDER_VIDEO_MEM_USED) / 1048576.0, 0.1)
	out["texture_mem_mb"] = snappedf(Performance.get_monitor(Performance.RENDER_TEXTURE_MEM_USED) / 1048576.0, 0.1)
	out["buffer_mem_mb"] = snappedf(Performance.get_monitor(Performance.RENDER_BUFFER_MEM_USED) / 1048576.0, 0.1)
	out["renderer"] = RenderingServer.get_video_adapter_name() + " / " + str(ProjectSettings.get_setting("rendering/renderer/rendering_method", ""))
	RenderingServer.viewport_set_measure_render_time(rid, false)
	return out


static func format_runtime(rt: Dictionary, tier: String = "mid") -> String:
	var B: Dictionary = TIERS.get(tier, TIERS.mid)
	var s: String = ""
	if float(rt.canvas_draws) > float(rt.draws) * 0.35:
		s += "[WARN] canvas/HUD is %.0f of %.0f draws -> fewer separate Controls/StyleBoxes/fonts, one theme + atlas, hide off-screen panels, avoid per-frame label text changes\n" % [rt.canvas_draws, rt.draws]
	s += "[runtime] %s\n  draws %.0f (3D visible %.0f + shadow %.0f + canvas/HUD %.0f) / budget %d   prims %.0fk / %dk   objects %.0f\n  fps %.0f  worst frame %.1f ms  process %.2f ms  render cpu %.2f ms  gpu %.2f ms\n  video mem %.1f MB (textures %.1f, buffers %.1f)" % [
		rt.renderer, rt.draws, rt.visible_draws, rt.shadow_draws, rt.canvas_draws, B.draws, float(rt.prims) / 1000.0, int(B.tris) / 1000, rt.objects,
		rt.fps, rt.worst_frame_ms, rt.process_ms, rt.cpu_ms, rt.gpu_ms, rt.video_mem_mb, rt.texture_mem_mb, rt.buffer_mem_mb]
	s += "\n  note: Compatibility (GLES3) counts the shadow pass under visible draws; canvas (HUD) draws are in the total."
	s += "\n  note: desktop GPU timings don't transfer to phones; draws/prims/memory do. Confirm fps on the device."
	return s
