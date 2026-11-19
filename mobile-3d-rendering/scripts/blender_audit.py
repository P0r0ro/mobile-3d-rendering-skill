"""Headless Blender audit of game assets against a render budget.

  blender -b --factory-startup --python blender_audit.py -- [--tier mid] [--json out.json] FILES...

FILES: .blend .glb .gltf .fbx .obj (each audited in a fresh scene). Prints one report per file
with per-object rows, then flags (ERROR / WARN / INFO) with what to do about each.
Tiers: low (old/entry phones), mid (Note 9 / Adreno 6xx / Mali-G7x class), high (flagship/PC).
Exit code 0 = no ERROR, 1 = at least one ERROR.
"""
import json
import math
import os
import sys

import bpy

TIERS = {
    #            tris/object  tris/file  tex px  mat/obj  bones  influences
    "low":  dict(obj_tris=5000, file_tris=20000, tex=1024, mats=2, bones=40, infl=2),
    "mid":  dict(obj_tris=15000, file_tris=60000, tex=2048, mats=3, bones=75, infl=4),
    "high": dict(obj_tris=50000, file_tris=250000, tex=4096, mats=6, bones=150, infl=4),
}


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {"tier": "mid", "json": None, "files": []}
    it = iter(argv)
    for a in it:
        if a == "--tier":
            out["tier"] = next(it)
        elif a == "--json":
            out["json"] = next(it)
        else:
            out["files"].append(os.path.abspath(a))
    return out


def load(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
        return
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError("unsupported file type " + ext)


def mesh_stats(obj, dg):
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    ngons = sum(1 for p in me.polygons if len(p.vertices) > 4)
    used_mats = {p.material_index for p in me.polygons}
    # unique vertices the GPU sees ~ loops split by uv/normal seams; approximate with loops cap
    st = dict(tris=tris, verts=len(me.vertices), loops=len(me.loops), ngons=ngons,
              uv_layers=len(me.uv_layers), color_attrs=len(me.color_attributes),
              used_slots=len(used_mats) if me.polygons else 0)
    # area-weighted "density": tris per m^2 of surface, flags over-tessellated small props
    area = sum(p.area for p in me.polygons)
    st["area_m2"] = area
    ev.to_mesh_clear()
    return st


def max_influences(obj):
    if not obj.vertex_groups or not any(m.type == "ARMATURE" for m in obj.modifiers):
        return 0
    deform = {vg.index for vg in obj.vertex_groups}
    return max((sum(1 for g in v.groups if g.group in deform and g.weight > 0.001)
                for v in obj.data.vertices), default=0)


def material_info(mat):
    info = dict(name=mat.name, images=[], blend=getattr(mat, "surface_render_method", "") or
                getattr(mat, "blend_method", ""), nodes=0)
    if mat.node_tree:
        info["nodes"] = len(mat.node_tree.nodes)
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image:
                img = n.image
                info["images"].append(dict(name=img.name, w=img.size[0], h=img.size[1],
                                           alpha=img.alpha_mode != "NONE" and img.channels == 4,
                                           packed=bool(img.packed_file)))
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf:
            a = bsdf.inputs.get("Alpha")
            info["alpha_used"] = bool(a and (a.is_linked or a.default_value < 0.999))
    return info


def audit_file(path, tier):
    load(path)
    B = TIERS[tier]
    dg = bpy.context.evaluated_depsgraph_get()
    rows, flags, mats = [], [], {}
    total_tris = 0
    draw_est = 0

    def flag(level, what, fix):
        flags.append(dict(level=level, what=what, fix=fix))

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        st = mesh_stats(obj, dg)
        st["name"] = obj.name
        st["infl"] = max_influences(obj)
        st["mods"] = [m.type for m in obj.modifiers]
        st["shape_keys"] = len(obj.data.shape_keys.key_blocks) - 1 if obj.data.shape_keys else 0
        st["scale"] = tuple(round(s, 3) for s in obj.scale)
        rows.append(st)
        total_tris += st["tris"]
        draw_est += max(1, st["used_slots"])
        for slot in obj.material_slots:
            if slot.material:
                mats[slot.material.name] = slot.material
        n = obj.name
        is_lod = any(t in n.lower() for t in ("lod1", "lod2", "lod3", "_lod", "-lod"))
        if st["tris"] > B["obj_tris"] and not is_lod:
            flag("WARN", f"{n}: {st['tris']} tris > {B['obj_tris']} for tier {tier}",
                 "decimate (Decimate modifier ratio, or Unsubdivide on grids), or add LODs "
                 "(_LOD0/_LOD1/_LOD2 at ~50%/25%) and let the engine switch them")
        if st["area_m2"] > 0 and st["tris"] / st["area_m2"] > 4000 and st["tris"] > 2000:
            flag("WARN", f"{n}: {st['tris'] / st['area_m2']:.0f} tris/m^2 (over-tessellated)",
                 "small props should be ~100-1000 tris/m^2; tiny triangles (<~8 px on screen) "
                 "waste GPU quad shading, worst on tile GPUs")
        if st["used_slots"] > B["mats"]:
            flag("WARN", f"{n}: {st['used_slots']} materials = {st['used_slots']} draw calls",
                 "atlas the textures and merge to 1-2 materials (palette/trim-sheet texture, or "
                 "vertex colours + one shared material)")
        if st["uv_layers"] == 0:
            flag("INFO", f"{n}: no UV map", "fine for vertex-colour/flat shading; textured or "
                 "lightmapped meshes need UV0 (and a non-overlapping UV1 for lightmaps)")
        if st["ngons"]:
            flag("INFO", f"{n}: {st['ngons']} n-gons", "triangulate before export for "
                 "predictable shading/normals (Triangulate modifier, keep normals)")
        if any(abs(s - 1.0) > 1e-3 for s in obj.scale) or any(abs(r) > 1e-4 for r in obj.rotation_euler):
            if obj.parent is None and obj.type == "MESH" and not obj.data.shape_keys:
                flag("INFO", f"{n}: unapplied transform {st['scale']}",
                     "Ctrl+A > All Transforms so engine scale = 1 (keeps physics, batching and "
                     "LOD distances sane)")
        if st["infl"] > B["infl"]:
            flag("WARN", f"{n}: up to {st['infl']} bone influences per vertex",
                 f"limit to {B['infl']} (Weights > Limit Total, then Normalize All)")
        if st["shape_keys"] > 8:
            flag("INFO", f"{n}: {st['shape_keys']} shape keys", "each active blend shape costs "
                 "vertex work every frame on mobile; bake unused ones away")
        heavy = [m for m in st["mods"] if m in ("SUBSURF", "MULTIRES", "REMESH")]
        if heavy:
            flag("WARN", f"{n}: {heavy} modifiers inflate the exported mesh",
                 "apply at a low level or remove; counts above are post-modifier")

    for obj in bpy.context.scene.objects:
        if obj.type == "ARMATURE":
            nb = len(obj.data.bones)
            if nb > B["bones"]:
                flag("WARN", f"{obj.name}: {nb} bones > {B['bones']}",
                     "drop finger/twist/face bones the camera never sees for crowd LODs; keep "
                     "a full rig only for the hero")
        if obj.type == "LIGHT":
            flag("INFO", f"light {obj.name} exported", "real-time lights in a model rarely "
                 "belong in-game on mobile; bake them into vertex colour/lightmap instead")

    tex_bytes = 0
    seen_img = set()
    minfo = [material_info(m) for m in mats.values()]
    for mi in minfo:
        if mi.get("alpha_used"):
            flag("INFO", f"material {mi['name']}: uses alpha",
                 "alpha blend = overdraw + sorting; alpha clip/discard disables early-Z on tile "
                 "GPUs. Prefer opaque geometry; else alpha-clip with a tight mesh shape")
        for im in mi["images"]:
            if im["name"] in seen_img:
                continue
            seen_img.add(im["name"])
            w, h = im["w"], im["h"]
            tex_bytes += w * h * 4 * 4 // 3  # rgba8 + mips
            if max(w, h) > B["tex"]:
                flag("WARN", f"texture {im['name']} {w}x{h} > {B['tex']}",
                     "downscale; on phones 1024 is plenty for most props, 2048 for hero/atlas")
            if w and h and (w & (w - 1) or h & (h - 1)):
                flag("INFO", f"texture {im['name']} {w}x{h} not power of two",
                     "POT sizes compress (ETC2/ASTC) and mip cleanly everywhere")
    if total_tris > B["file_tris"]:
        flag("WARN", f"file total {total_tris} tris > {B['file_tris']}",
             "this asset alone eats a big share of a mobile frame budget; LOD or split it")
    if len(rows) > 1 and draw_est > 4:
        flag("INFO", f"~{draw_est} draw calls if exported as-is",
             "each mesh object is its own draw. Static parts: join objects sharing a material "
             "(Ctrl+J) or let the engine merge/instance them; keep only animated parts "
             "(wheels, doors) separate")
    actions = list(bpy.data.actions)
    return dict(file=path, tier=tier, objects=rows, total_tris=total_tris,
                draw_calls_est=draw_est, materials=len(mats), textures=len(seen_img),
                tex_mb_uncompressed=round(tex_bytes / 2**20, 1),
                tex_mb_astc6x6=round(tex_bytes / 2**20 / 9.0, 1),
                actions=len(actions), flags=flags)


def report(r):
    print(f"\n=== {os.path.basename(r['file'])}  (tier {r['tier']})")
    print(f"tris {r['total_tris']}  draw calls ~{r['draw_calls_est']}  materials {r['materials']}  "
          f"textures {r['textures']} ({r['tex_mb_uncompressed']} MB raw / ~{r['tex_mb_astc6x6']} MB "
          f"ASTC 6x6)  actions {r['actions']}")
    print(f"{'object':32s} {'tris':>7s} {'verts':>7s} {'mats':>4s} {'uv':>2s} {'col':>3s} {'infl':>4s}")
    for o in sorted(r["objects"], key=lambda o: -o["tris"])[:25]:
        print(f"{o['name'][:32]:32s} {o['tris']:7d} {o['verts']:7d} {o['used_slots']:4d} "
              f"{o['uv_layers']:2d} {o['color_attrs']:3d} {o['infl']:4d}")
    if len(r["objects"]) > 25:
        print(f"... {len(r['objects']) - 25} more objects")
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    for f in sorted(r["flags"], key=lambda f: order[f["level"]]):
        print(f"[{f['level']}] {f['what']}\n        -> {f['fix']}")
    if not r["flags"]:
        print("[OK] within budget")


def main():
    a = args()
    if a["tier"] not in TIERS:
        print("tier must be one of", list(TIERS))
        sys.exit(2)
    if not a["files"]:
        print(__doc__)
        sys.exit(2)
    results = []
    for f in a["files"]:
        try:
            r = audit_file(f, a["tier"])
        except Exception as e:  # keep going over a batch
            r = dict(file=f, tier=a["tier"], objects=[], total_tris=0, draw_calls_est=0,
                     materials=0, textures=0, tex_mb_uncompressed=0, tex_mb_astc6x6=0, actions=0,
                     flags=[dict(level="ERROR", what=f"could not load: {e}", fix="check the file")])
        report(r)
        results.append(r)
    if a["json"]:
        with open(a["json"], "w") as fh:
            json.dump(results, fh, indent=1)
    sys.exit(1 if any(f["level"] == "ERROR" for r in results for f in r["flags"]) else 0)


main()
