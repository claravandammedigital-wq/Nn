#!/usr/bin/env python3
"""
"What happens after you leave your cat to go to work?"
Low-poly stylised 3D short, built and rendered with Blender (bpy module) + Eevee.

Usage (from this folder):
    python3 scene.py --preview 1,5              # still images  -> build/preview/
    python3 scene.py --render all               # every frame   -> build/frames/sN/0001.png ...
    python3 scene.py --render 4 --scale 50      # quick half-resolution test of scene 4
Then:
    python3 compose.py                          # ffmpeg: assemble + on-screen texts -> MP4

Everything editable (texts, clock times, durations, cat colours, lighting) lives in config.json.
Scene animation timings below are written in seconds (see the `T()` helper).
"""
import argparse
import json
import math
import os
import random

import bpy
import bmesh
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
FPS = CFG["video"]["fps"]
D = math.radians


# ----------------------------------------------------------------------------- helpers
def srgb(h, a=1.0):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return (*lin, a)


def T(sec):
    """seconds (scene-local) -> frame number (frames start at 1)"""
    return 1 + round(sec * FPS)


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def clamp01(t):
    return max(0.0, min(1.0, t))


COLL = None


def link(ob):
    COLL.objects.link(ob)
    return ob


def empty(name, loc=(0, 0, 0), rot=(0, 0, 0), parent=None):
    ob = link(bpy.data.objects.new(name, None))
    ob.empty_display_size = 0.05
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    return ob


def mesh(name, kind, dims, loc=(0, 0, 0), rot=(0, 0, 0), mat=None, parent=None,
         seg=10, rings=7, flat=True, bevel=0.0, half=False):
    """Create a primitive with its size baked into the mesh. dims = half-extents."""
    bm = bmesh.new()
    if kind == "sphere":
        bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=1.0)
    elif kind == "ico":
        bmesh.ops.create_icosphere(bm, subdivisions=seg, radius=1.0)
    elif kind == "cube":
        bmesh.ops.create_cube(bm, size=2.0)
    elif kind == "cyl":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                              radius1=1.0, radius2=1.0, depth=2.0)
    elif kind == "cone":
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                              radius1=1.0, radius2=0.0, depth=2.0)
    elif kind == "plane":
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
    if half:  # keep upper half (z >= 0)
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.z < -1e-4], context="VERTS")
    bmesh.ops.scale(bm, vec=dims, verts=bm.verts)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = not flat
    ob = link(bpy.data.objects.new(name, me))
    if mat is not None:
        me.materials.append(mat)
    ob.parent = parent
    ob.location = loc
    ob.rotation_euler = rot
    if bevel > 0:
        m = ob.modifiers.new("bevel", "BEVEL")
        m.width = bevel
        m.segments = 2
        m.limit_method = "ANGLE"
    return ob


def box(name, x0, x1, y0, y1, z0, z1, mat, bevel=0.0, parent=None):
    return mesh(name, "cube", ((x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2),
                loc=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), mat=mat, bevel=bevel, parent=parent)


# ----------------------------------------------------------------------------- materials
MATS = {}


def mat(name, color, rough=0.8, emit=0.0, alpha=None, spec=0.3):
    if name in MATS:
        return MATS[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    col = srgb(color) if isinstance(color, str) else color
    b.inputs["Base Color"].default_value = col
    b.inputs["Roughness"].default_value = rough
    b.inputs["Specular IOR Level"].default_value = spec
    if emit:
        b.inputs["Emission Color"].default_value = col
        b.inputs["Emission Strength"].default_value = emit
    if alpha is not None:
        b.inputs["Alpha"].default_value = alpha
        m.surface_render_method = "BLENDED"
    MATS[name] = m
    return m


def fur_mat(name, base, stripe, cream, axis="Y", scale=7.0, distortion=2.5, belly=None, stripe_w=0.38):
    """Tabby fur: wave-texture stripes + optional cream belly (object-space Z threshold)."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    n, l = nt.nodes, nt.links
    bsdf = n["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.85
    bsdf.inputs["Specular IOR Level"].default_value = 0.2
    tc = n.new("ShaderNodeTexCoord")
    wave = n.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = axis
    wave.inputs["Scale"].default_value = scale * CFG["cat"].get("stripe_density", 1.0)
    wave.inputs["Distortion"].default_value = distortion
    wave.inputs["Detail"].default_value = 1.0
    wave.inputs["Detail Scale"].default_value = 1.5
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = srgb(stripe)
    ramp.color_ramp.elements[1].position = stripe_w
    ramp.color_ramp.elements[1].color = srgb(base)
    l.new(tc.outputs["Object"], wave.inputs["Vector"])
    l.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    out = ramp.outputs["Color"]
    if belly is not None:
        sep = n.new("ShaderNodeSeparateXYZ")
        l.new(tc.outputs["Object"], sep.inputs["Vector"])
        lt = n.new("ShaderNodeMath")
        lt.operation = "LESS_THAN"
        lt.inputs[1].default_value = belly
        l.new(sep.outputs["Z"], lt.inputs[0])
        mix = n.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        l.new(lt.outputs[0], mix.inputs["Factor"])
        l.new(out, mix.inputs[6])
        mix.inputs[7].default_value = srgb(cream)
        out = mix.outputs[2]
    l.new(out, bsdf.inputs["Base Color"])
    return m


def gradient_emit_mat(name, top, bottom, z0, z1, strength=1.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    n, l = nt.nodes, nt.links
    for x in list(n):
        n.remove(x)
    outn = n.new("ShaderNodeOutputMaterial")
    em = n.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    tc = n.new("ShaderNodeTexCoord")
    sep = n.new("ShaderNodeSeparateXYZ")
    mr = n.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = z0
    mr.inputs["From Max"].default_value = z1
    ramp = n.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = srgb(bottom)
    ramp.color_ramp.elements[1].color = srgb(top)
    l.new(tc.outputs["Object"], sep.inputs["Vector"])
    l.new(sep.outputs["Z"], mr.inputs["Value"])
    l.new(mr.outputs["Result"], ramp.inputs["Fac"])
    l.new(ramp.outputs["Color"], em.inputs["Color"])
    l.new(em.outputs[0], outn.inputs["Surface"])
    m["ramp"] = ramp.name
    m["em"] = em.name
    return m


def building_mat(name, wall, win_strength):
    """Building facade: brick texture used as a grid of windows that can light up."""
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    n, l = nt.nodes, nt.links
    bsdf = n["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.9
    tc = n.new("ShaderNodeTexCoord")
    br = n.new("ShaderNodeTexBrick")
    br.offset = 0.0
    br.squash = 1.0
    br.inputs["Scale"].default_value = 1.0
    br.inputs["Mortar Size"].default_value = 0.35
    br.inputs["Brick Width"].default_value = 0.9
    br.inputs["Row Height"].default_value = 1.1
    br.inputs["Color1"].default_value = srgb("#FFD27A")
    br.inputs["Color2"].default_value = srgb("#9FB6CC")
    br.inputs["Mortar"].default_value = srgb(wall)
    l.new(tc.outputs["Object"], br.inputs["Vector"])
    l.new(br.outputs["Color"], bsdf.inputs["Base Color"])
    inv = n.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    l.new(br.outputs["Fac"], inv.inputs[1])
    mul = n.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = win_strength
    l.new(inv.outputs[0], mul.inputs[0])
    l.new(br.outputs["Color"], bsdf.inputs["Emission Color"])
    l.new(mul.outputs[0], bsdf.inputs["Emission Strength"])
    m["mul"] = mul.name
    return m


# ----------------------------------------------------------------------------- the cat
class Cat:
    """Original low-poly orange tabby. Forward = +Y, up = +Z, root on the floor."""

    def __init__(self, c):
        fur, stripe, cream = c["fur"], c["stripes"], c["cream"]
        self.m_body = fur_mat("cat_body", fur, stripe, cream, axis="Y", scale=6.5, belly=-0.055)
        self.m_head = fur_mat("cat_head", fur, stripe, cream, axis="X", scale=7.0, distortion=4.0, stripe_w=0.2)
        self.m_leg = fur_mat("cat_leg", fur, stripe, cream, axis="Z", scale=9.0, distortion=1.0, stripe_w=0.3)
        self.m_fur = mat("cat_fur", fur, 0.85, spec=0.2)
        self.m_stripe = mat("cat_stripe", stripe, 0.85, spec=0.2)
        self.m_cream = mat("cat_cream", cream, 0.85, spec=0.2)
        self.m_eye = mat("cat_eye", c["eyes"], 0.15, emit=0.25, spec=0.6)
        self.m_pupil = mat("cat_pupil", c["pupil"], 0.1, spec=0.8)
        self.m_white = mat("cat_hl", "#FFFFFF", 0.2, emit=3.0)
        self.m_nose = mat("cat_nose", c["nose"], 0.4)
        self.m_inner = mat("cat_inner_ear", c["inner_ear"], 0.8)
        self.m_dark = mat("cat_line", "#3A1E0E", 0.6)
        self.m_whisk = mat("cat_whisker", "#FFF8EE", 0.5, emit=0.3)

        self.root = empty("cat_root")
        self.body = empty("cat_body_pv", (0, 0, 0.22), parent=self.root)
        mesh("cat_torso", "sphere", (0.135, 0.25, 0.125), mat=self.m_body, parent=self.body, seg=12, rings=8)
        mesh("cat_chest", "sphere", (0.1, 0.07, 0.1), (0, 0.19, -0.005), mat=self.m_cream, parent=self.body, seg=10, rings=6)

        # head
        self.head = empty("cat_head_pv", (0, 0.2, 0.09), parent=self.body)
        mesh("cat_skull", "sphere", (0.15, 0.13, 0.125), (0, 0.07, 0.08), mat=self.m_head, parent=self.head, seg=12, rings=8)
        for s in (-1, 1):
            mesh(f"cat_cheek{s}", "sphere", (0.075, 0.065, 0.05), (s * 0.085, 0.1, 0.025), (0, 0, s * D(20)),
                 mat=self.m_fur, parent=self.head, seg=8, rings=5)
        mesh("cat_muzzle", "sphere", (0.06, 0.04, 0.042), (0, 0.18, 0.035), mat=self.m_cream, parent=self.head, seg=10, rings=6)
        mesh("cat_chin", "sphere", (0.035, 0.03, 0.025), (0, 0.165, -0.0), mat=self.m_cream, parent=self.head, seg=8, rings=5)
        mesh("cat_nose", "sphere", (0.02, 0.012, 0.013), (0, 0.215, 0.066), mat=self.m_nose, parent=self.head, seg=6, rings=4)
        # ears
        self.ears = []
        for s in (-1, 1):
            pv = empty(f"cat_ear_pv{s}", (s * 0.085, 0.05, 0.17), (D(-8), s * D(22), 0), parent=self.head)
            mesh(f"cat_ear{s}", "cone", (0.055, 0.03, 0.055), (0, 0, 0.05), mat=self.m_fur, parent=pv, seg=4)
            mesh(f"cat_ear_in{s}", "cone", (0.035, 0.012, 0.04), (0, 0.016, 0.04), mat=self.m_inner, parent=pv, seg=4)
            self.ears.append(pv)
        # eyes
        self.eyes, self.irises, self.pupils, self.lids, self.closed = [], [], [], [], []
        for s in (-1, 1):
            pv = empty(f"cat_eye_pv{s}", (s * 0.062, 0.162, 0.095), (0, 0, s * D(-16)), parent=self.head)
            iris = empty(f"cat_iris_pv{s}", parent=pv)
            mesh(f"cat_iris{s}", "sphere", (0.041, 0.02, 0.045), mat=self.m_eye, parent=iris, seg=12, rings=8, flat=False)
            pup = empty(f"cat_pupil_pv{s}", (0, 0.009, 0), parent=iris)
            mesh(f"cat_pupil{s}", "sphere", (0.012, 0.014, 0.033), mat=self.m_pupil, parent=pup, seg=10, rings=6, flat=False)
            mesh(f"cat_hl{s}", "sphere", (0.009, 0.006, 0.009), (-0.014, 0.021, 0.02), mat=self.m_white, parent=iris, seg=6, rings=4)
            lid = mesh(f"cat_lid{s}", "sphere", (0.045, 0.024, 0.049), mat=self.m_fur, parent=pv, seg=12, rings=8, half=True)
            cl = mesh(f"cat_closed{s}", "sphere", (0.039, 0.014, 0.006), (0, 0.013, -0.004), mat=self.m_dark, parent=pv, seg=10, rings=4)
            self.eyes.append(pv); self.irises.append(iris); self.pupils.append(pup); self.lids.append(lid); self.closed.append(cl)
        # whiskers
        for s in (-1, 1):
            for i, a in enumerate((-12, 0, 12)):
                mesh(f"cat_wh{s}{i}", "cyl", (0.0018, 0.0018, 0.075), (s * 0.1, 0.19, 0.025 + i * 0.008),
                     (D(90 + a * 0.4), 0, s * D(-80 + a)), mat=self.m_whisk, parent=self.head, seg=4)
        # legs
        self.legs = {}
        for name, sx, sy in (("fl", -1, 1), ("fr", 1, 1), ("bl", -1, -1), ("br", 1, -1)):
            pv = empty(f"cat_leg_{name}", (sx * 0.075, sy * 0.15, -0.02), parent=self.body)
            mesh(f"cat_leg_m_{name}", "cyl", (0.036, 0.036, 0.085), (0, 0, -0.085), mat=self.m_leg, parent=pv, seg=8)
            mesh(f"cat_paw_{name}", "sphere", (0.043, 0.052, 0.03), (0, 0.012, -0.172), mat=self.m_cream, parent=pv, seg=8, rings=5)
            if sy < 0:
                mesh(f"cat_thigh_{name}", "sphere", (0.06, 0.095, 0.095), (0, -0.01, -0.02), mat=self.m_body, parent=pv, seg=10, rings=6)
            self.legs[name] = pv
        # tail: chain of segments
        self.tail = []
        parent = empty("cat_tail_base", (0, -0.235, 0.05), parent=self.body)
        self.tail_base = parent
        n = 8
        for i in range(n):
            pv = empty(f"cat_tail{i}", (0, 0, 0) if i == 0 else (0, -0.058, 0), parent=parent)
            r = lerp(0.033, 0.024, i / (n - 1))
            m = self.m_stripe if i in (2, 4, 6, 7) else self.m_fur
            mesh(f"cat_tail_m{i}", "cyl", (r, r, 0.034), (0, -0.029, 0), (D(90), 0, 0), mat=m, parent=pv, seg=8)
            mesh(f"cat_tail_j{i}", "sphere", (r, r, r), mat=m, parent=pv, seg=8, rings=5)
            self.tail.append(pv)
            parent = pv
        self.parts = [self.root, self.body, self.head, self.tail_base] + self.ears + self.eyes + self.irises + \
            self.pupils + self.lids + self.closed + list(self.legs.values()) + self.tail
        self.rest = {p.name: (p.location.copy(), p.rotation_euler.copy(), p.scale.copy()) for p in self.parts}
        self.reset()

    # --- pose building blocks ---------------------------------------------------
    def reset(self):
        for p in self.parts:
            l, r, s = self.rest[p.name]
            p.location, p.rotation_euler, p.scale = l.copy(), r.copy(), s.copy()
        self.set_eyes(1.0, 0.0)
        self.tail_shape(-50, 3, -12)

    def set_eyes(self, openness=1.0, lid=0.0, pupil=1.0, look=(0, 0)):
        """openness 0..1 (0 = closed line), lid 0..1 (1 = half-lidded smug), pupil width multiplier"""
        for i in range(2):
            o = max(openness, 0.04)
            self.irises[i].scale = (1, 1, o)
            self.closed[i].scale = (1, 1, 1) if openness < 0.2 else (0.001, 0.001, 0.001)
            self.lids[i].rotation_euler = (D(lerp(115, 8, lid)), 0, 0)
            self.pupils[i].scale = (pupil, 1, min(1.0, 0.7 + 0.3 * pupil))
            self.pupils[i].location = (look[0] * 0.012, 0.01, look[1] * 0.012)

    def tail_shape(self, base=-55, bend=-10, curl=6, yaw=0.0, wave=0.0, phase=0.0):
        self.tail[0].rotation_euler = (D(base), 0, D(yaw))
        for i, seg in enumerate(self.tail[1:], 1):
            w = wave * math.sin(phase - i * 0.7)
            c = curl * max(0, i - 4)
            seg.rotation_euler = (D(bend + c), 0, D(w))

    def sit(self):
        self.body.location = (0, 0.05, 0.215)
        self.body.rotation_euler = (D(50), 0, 0)
        self.head.rotation_euler = (D(-44), 0, 0)
        for k in ("fl", "fr"):
            self.legs[k].rotation_euler = (D(-52), 0, 0)
            self.legs[k].scale = (1, 1, 1.5)
        for k in ("bl", "br"):
            self.legs[k].rotation_euler = (D(0), 0, 0)
            self.legs[k].scale = (1, 1, 0.7)
        # tail wraps on the floor around the right side
        self.tail[0].rotation_euler = (D(-10), 0, D(0))
        for i, seg in enumerate(self.tail[1:], 1):
            seg.rotation_euler = (D(8 if i < 3 else 0), 0, D(-20))

    def loaf(self):
        """Lying down, legs tucked (sleeping on the sofa)."""
        self.body.location = (0, 0, 0.12)
        self.body.rotation_euler = (0, 0, 0)
        self.body.scale = (1.08, 1.0, 0.95)
        for k in ("fl", "fr"):
            self.legs[k].rotation_euler = (D(-90), 0, 0)
            self.legs[k].scale = (1, 1, 0.55)
        for k in ("bl", "br"):
            self.legs[k].rotation_euler = (D(80), 0, 0)
            self.legs[k].scale = (1, 1, 0.45)
        self.head.location = (0, 0.21, 0.02)
        self.head.rotation_euler = (D(12), 0, 0)
        # tail curled along the side
        self.tail[0].rotation_euler = (D(25), 0, D(-35))
        for i, seg in enumerate(self.tail[1:], 1):
            seg.rotation_euler = (D(0), 0, D(-24))

    def flopped(self):
        """Lying on its side, dramatic."""
        self.root.rotation_euler.y = D(90)
        self.body.location = (0, 0, 0.12)
        self.legs["fl"].rotation_euler = (D(35), D(-10), 0)
        self.legs["fr"].rotation_euler = (D(20), D(10), 0)
        self.legs["bl"].rotation_euler = (D(-25), D(-10), 0)
        self.legs["br"].rotation_euler = (D(-10), D(10), 0)
        self.head.rotation_euler = (D(-40), 0, D(0))
        self.tail[0].rotation_euler = (D(0), 0, D(0))
        for seg in self.tail[1:]:
            seg.rotation_euler = (0, 0, D(-6))

    def key(self, f, interp=None):
        for p in self.parts:
            p.keyframe_insert("location", frame=f)
            p.keyframe_insert("rotation_euler", frame=f)
            p.keyframe_insert("scale", frame=f)


# ----------------------------------------------------------------------------- the apartment
class World:
    def __init__(self):
        R = CFG["room"]
        self.m = {
            "wall": mat("wall", R["walls"], 0.95, spec=0.1),
            "trim": mat("trim", "#FBF6EE", 0.6),
            "sofa": mat("sofa", R["sofa"], 0.95, spec=0.1),
            "sofa_dark": mat("sofa_dark", "#245A5D", 0.95, spec=0.1),
            "pillow": mat("pillow", R["pillow"], 0.9),
            "pillow2": mat("pillow2", "#E8D6BD", 0.9),
            "rug": mat("rug", R["rug"], 1.0, spec=0.05),
            "rug_b": mat("rug_border", "#C0623E", 1.0, spec=0.05),
            "wood": mat("wood", R["table"], 0.55),
            "wood_dark": mat("wood_dark", "#5A3A26", 0.6),
            "door": mat("door", R["door"], 0.6),
            "metal": mat("metal", "#D9B25A", 0.25, spec=0.8),
            "curtain": mat("curtain", R["curtains"], 0.95),
            "leaf": mat("leaf", R["plant"], 0.7),
            "leaf2": mat("leaf2", "#5DAE5C", 0.7),
            "pot": mat("pot", R["pot"], 0.8),
            "glass": mat("glass", "#CFEFFF", 0.05, alpha=0.35, spec=1.0),
            "water": mat("water", "#7FC8F0", 0.05, alpha=0.6, spec=1.0),
            "puddle": mat("puddle", "#9BD6F5", 0.02, alpha=0.55, spec=1.0),
            "hall": mat("hall", "#FFE2B0", 1.0, emit=6.0),
            "lampshade": mat("lampshade", "#FFF0D6", 0.9, emit=0.0),
            "art1": mat("art1", "#E9B04B", 0.9),
            "art2": mat("art2", "#3D6E8F", 0.9),
            "art3": mat("art3", "#E07A5F", 0.9),
            "bird": mat("bird", "#4A4F5C", 0.8),
            "bird_belly": mat("bird_belly", "#E8E2D8", 0.8),
            "beam": mat("beam", "#FFE2A8", 1.0, emit=1.5, alpha=0.10),
            "dust": mat("dust", "#F3E6D3", 1.0, alpha=0.6),
            "zzz": mat("zzz", "#FFFFFF", 0.5, emit=1.5),
            "mat": mat("doormat", "#8A5A3B", 1.0),
        }
        self.build_room()
        self.build_outside()
        self.build_furniture()
        self.build_lights()

    def floor_mat(self):
        m = bpy.data.materials.new("floor")
        m.use_nodes = True
        n, l = m.node_tree.nodes, m.node_tree.links
        bsdf = n["Principled BSDF"]
        bsdf.inputs["Roughness"].default_value = 0.45
        tc = n.new("ShaderNodeTexCoord")
        br = n.new("ShaderNodeTexBrick")
        br.inputs["Scale"].default_value = 1.0
        br.inputs["Brick Width"].default_value = 1.6
        br.inputs["Row Height"].default_value = 0.22
        br.inputs["Mortar Size"].default_value = 0.006
        br.inputs["Color1"].default_value = srgb(CFG["room"]["floor"])
        br.inputs["Color2"].default_value = srgb("#9A6741")
        br.inputs["Mortar"].default_value = srgb("#6B4429")
        br.inputs["Bias"].default_value = 0.0
        l.new(tc.outputs["Object"], br.inputs["Vector"])
        l.new(br.outputs["Color"], bsdf.inputs["Base Color"])
        return m

    def build_room(self):
        w, t = self.m["wall"], self.m["trim"]
        box("floor", -3.6, 3.6, -4.5, 3.6, -0.05, 0.0, self.floor_mat())
        # back wall (y=3.5) with window opening x[-0.3,2.5] z[0.75,2.55]
        y0, y1 = 3.5, 3.65
        box("bw_l", -3.6, -0.3, y0, y1, 0, 3.2, w)
        box("bw_r", 2.5, 3.6, y0, y1, 0, 3.2, w)
        box("bw_b", -0.3, 2.5, y0, y1, 0, 0.75, w)
        box("bw_t", -0.3, 2.5, y0, y1, 2.55, 3.2, w)
        # window frame + sill
        box("win_frame_l", -0.38, -0.3, 3.45, 3.66, 0.75, 2.6, t)
        box("win_frame_r", 2.5, 2.58, 3.45, 3.66, 0.75, 2.6, t)
        box("win_frame_t", -0.38, 2.58, 3.45, 3.66, 2.55, 2.63, t)
        box("win_mull_v", 1.07, 1.13, 3.56, 3.6, 0.75, 2.55, t)
        box("win_mull_h", -0.3, 2.5, 3.56, 3.6, 1.72, 1.77, t)
        box("win_sill", -0.45, 2.65, 3.08, 3.62, 0.68, 0.75, t, bevel=0.01)
        box("win_glass", -0.3, 2.5, 3.575, 3.585, 0.75, 2.55, mat("winglass", "#DDF2FF", 0.02, alpha=0.06, spec=1.0))
        # curtains
        box("curtain_l", -0.85, -0.4, 3.3, 3.42, 0.3, 2.85, self.m["curtain"], bevel=0.04)
        box("curtain_r", 2.6, 3.05, 3.3, 3.42, 0.3, 2.85, self.m["curtain"], bevel=0.04)
        box("curtain_rod", -1.0, 3.2, 3.36, 3.4, 2.85, 2.89, self.m["wood_dark"])
        # left wall (x=-3.5) with door opening y[-0.45,0.45] z[0,2.1]
        x0, x1 = -3.65, -3.5
        box("lw_a", x0, x1, -4.5, -0.45, 0, 3.2, w)
        box("lw_b", x0, x1, 0.45, 3.65, 0, 3.2, w)
        box("lw_c", x0, x1, -0.45, 0.45, 2.1, 3.2, w)
        box("door_trim_a", -3.52, -3.45, -0.55, -0.45, 0, 2.18, t)
        box("door_trim_b", -3.52, -3.45, 0.45, 0.55, 0, 2.18, t)
        box("door_trim_c", -3.52, -3.45, -0.55, 0.55, 2.1, 2.18, t)
        # door on a hinge at y=-0.45
        self.door = empty("door_hinge", (-3.56, -0.45, 0))
        mesh("door_panel", "cube", (0.03, 0.448, 1.048), (0, 0.449, 1.05), mat=self.m["door"], parent=self.door, bevel=0.008)
        mesh("door_inset1", "cube", (0.006, 0.34, 0.38), (0.033, 0.449, 1.5), mat=mat("door_inset", "#536879", 0.6), parent=self.door)
        mesh("door_inset2", "cube", (0.006, 0.34, 0.38), (0.033, 0.449, 0.58), mat=MATS["door_inset"], parent=self.door)
        mesh("door_knob", "sphere", (0.035, 0.035, 0.035), (0.07, 0.8, 1.0), mat=self.m["metal"], parent=self.door, flat=False)
        mesh("door_knob_in", "cyl", (0.012, 0.012, 0.03), (0.045, 0.8, 1.0), (0, D(90), 0), mat=self.m["metal"], parent=self.door)
        # hallway behind the door (bright)
        box("hall_floor", -5.5, -3.65, -1.5, 1.5, -0.05, 0.0, mat("hall_floor", "#C9A27A", 0.6))
        box("hall_wall", -5.6, -5.5, -1.5, 1.5, 0, 3.0, self.m["hall"])
        box("hall_side1", -5.5, -3.65, -1.6, -1.5, 0, 3.0, mat("hall_side", "#F6E6CC", 1.0, emit=1.0))
        box("hall_side2", -5.5, -3.65, 1.5, 1.6, 0, 3.0, MATS["hall_side"])
        box("doormat", -3.45, -2.85, -0.4, 0.4, 0.0, 0.012, self.m["mat"])
        # right wall (x=3.5)
        box("rw", 3.5, 3.65, -4.5, 3.65, 0, 3.2, w)
        # baseboards
        box("bb_back_l", -3.5, -0.3, 3.47, 3.5, 0, 0.1, t)
        box("bb_back_r", 2.5, 3.5, 3.47, 3.5, 0, 0.1, t)
        box("bb_back_m", -0.3, 2.5, 3.47, 3.5, 0, 0.1, t)
        box("bb_right", 3.47, 3.5, -4.5, 3.5, 0, 0.1, t)
        box("bb_left_a", -3.5, -3.47, -4.5, -0.55, 0, 0.1, t)
        box("bb_left_b", -3.5, -3.47, 0.55, 3.5, 0, 0.1, t)
        # wall art above the sofa (right wall)
        box("art_frame1", 3.44, 3.5, -0.1, 0.7, 1.35, 2.15, self.m["wood_dark"])
        box("art_canvas1", 3.43, 3.45, -0.05, 0.65, 1.4, 2.1, mat("canvas", "#F3EBDD", 0.9))
        mesh("art_sun", "cyl", (0.17, 0.17, 0.005), (3.425, 0.3, 1.82), (0, D(90), 0), mat=self.m["art1"], seg=16)
        box("art_hill", 3.425, 3.43, -0.05, 0.65, 1.4, 1.6, self.m["art2"])
        box("art_frame2", 3.44, 3.5, 1.0, 1.5, 1.45, 2.05, self.m["wood_dark"])
        box("art_canvas2", 3.43, 3.45, 1.04, 1.46, 1.49, 2.01, self.m["art3"])
        # small wall art near the door
        box("art_frame3", -3.5, -3.44, 1.2, 1.8, 1.3, 1.9, self.m["wood_dark"])
        box("art_canvas3", -3.45, -3.43, 1.24, 1.76, 1.34, 1.86, self.m["art2"])
        # coat hooks + scarf by the door
        box("hook_rail", -3.5, -3.45, -1.5, -0.8, 1.65, 1.72, self.m["wood_dark"])
        box("scarf", -3.47, -3.4, -1.32, -1.18, 0.95, 1.65, self.m["pillow"], bevel=0.02)

    def build_outside(self):
        L = CFG["lighting"]["morning"]
        self.sky = gradient_emit_mat("sky", L["sky_top"], L["sky_bottom"], -2, 14, strength=1.0)
        mesh("sky_plane", "cube", (40, 0.1, 20), (1, 34, 6), mat=self.sky)
        rnd = random.Random(7)
        self.bmats = []
        cols = ["#8A93B5", "#A39BB8", "#7C8CA8", "#B5A8A0", "#9AA8B8", "#8F9EB0"]
        for i, c in enumerate(cols):
            self.bmats.append(building_mat(f"bld{i}", c, 0.0))
        x = -9.0
        while x < 12:
            wdt = rnd.uniform(1.2, 2.6)
            h = rnd.uniform(3, 11)
            y = rnd.uniform(10, 18)
            mesh(f"bld_{x:.1f}", "cube", (wdt / 2, 1.5, h / 2), (x + wdt / 2, y, h / 2 - 3.0),
                 mat=rnd.choice(self.bmats))
            x += wdt + rnd.uniform(0.1, 0.6)
        # near building ledge & tree tops
        box("outside_ground", -15, 15, 3.7, 30, -3.2, -3.0, mat("ground", "#6F8C6A", 1.0))
        for i in range(6):
            mesh(f"tree{i}", "ico", (0.9, 0.9, 0.8), (-2 + i * 1.3, 6.5 + (i % 2) * 0.8, -1.6 + (i % 3) * 0.3),
                 mat=self.m["leaf2"] if i % 2 else self.m["leaf"], seg=1)

    def build_furniture(self):
        M = self.m
        # sofa against the right wall, facing -X
        sx = 3.0
        box("sofa_base", 2.55, 3.45, -0.45, 1.95, 0.08, 0.36, M["sofa_dark"], bevel=0.03)
        self.cushions = [box("sofa_seat1", 2.58, 3.3, -0.33, 0.73, 0.36, 0.5, M["sofa"], bevel=0.04),
                         box("sofa_seat2", 2.58, 3.3, 0.77, 1.83, 0.36, 0.5, M["sofa"], bevel=0.04)]
        box("sofa_back", 3.22, 3.45, -0.45, 1.95, 0.36, 1.0, M["sofa_dark"], bevel=0.04)
        box("sofa_backc1", 3.08, 3.25, -0.33, 0.73, 0.5, 0.92, M["sofa"], bevel=0.05)
        box("sofa_backc2", 3.08, 3.25, 0.77, 1.83, 0.5, 0.92, M["sofa"], bevel=0.05)
        box("sofa_arm1", 2.55, 3.45, -0.62, -0.42, 0.08, 0.68, M["sofa_dark"], bevel=0.05)
        box("sofa_arm2", 2.55, 3.45, 1.92, 2.12, 0.08, 0.68, M["sofa_dark"], bevel=0.05)
        for i, (x, y) in enumerate(((2.62, -0.55), (3.38, -0.55), (2.62, 2.05), (3.38, 2.05))):
            mesh(f"sofa_leg{i}", "cyl", (0.025, 0.025, 0.04), (x, y, 0.04), mat=M["wood_dark"], seg=6)
        self.pillow = mesh("pillow", "cube", (0.09, 0.2, 0.18), (3.05, 1.65, 0.66), (0, D(-12), D(-10)),
                           mat=M["pillow"], bevel=0.06)
        self.pillow2 = mesh("pillow2", "cube", (0.08, 0.18, 0.16), (3.05, -0.15, 0.64), (0, D(-12), D(15)),
                            mat=M["pillow2"], bevel=0.06)
        self.throw = box("throw", 2.6, 3.32, 1.1, 1.6, 0.5, 0.52, M["rug_b"], bevel=0.01)
        # rug + coffee table
        box("rug_border", 0.15, 2.4, -0.7, 2.2, 0.0, 0.008, M["rug_b"])
        box("rug", 0.25, 2.3, -0.6, 2.1, 0.0, 0.011, M["rug"])
        self.table_top = box("table_top", 1.0, 1.95, 0.25, 1.35, 0.39, 0.44, M["wood"], bevel=0.015)
        for i, (x, y) in enumerate(((1.07, 0.32), (1.88, 0.32), (1.07, 1.28), (1.88, 1.28))):
            mesh(f"table_leg{i}", "cyl", (0.025, 0.025, 0.195), (x, y, 0.195), mat=M["wood_dark"], seg=6)
        box("table_shelf", 1.07, 1.88, 0.32, 1.28, 0.12, 0.14, M["wood_dark"])
        box("book1", 1.2, 1.5, 0.45, 0.7, 0.14, 0.18, M["art3"])
        box("book2", 1.22, 1.48, 0.47, 0.68, 0.18, 0.21, M["art2"])
        mesh("bowl", "sphere", (0.12, 0.12, 0.05), (1.7, 1.05, 0.46), mat=M["pot"], seg=10, rings=6, half=True)
        # glass on the table (animated in scene 5)
        self.glass = empty("glass_root", (1.55, 0.55, 0.44))
        mesh("glass_cup", "cyl", (0.045, 0.045, 0.065), (0, 0, 0.065), mat=M["glass"], parent=self.glass, seg=14, flat=False)
        mesh("glass_water", "cyl", (0.04, 0.04, 0.04), (0, 0, 0.045), mat=M["water"], parent=self.glass, seg=14, flat=False)
        self.puddle = mesh("puddle", "cyl", (0.22, 0.16, 0.003), (0.75, 0.1, 0.012), mat=M["puddle"], seg=14, flat=False)
        # plant (left of the window)
        box("pot", -1.25, -0.85, 2.85, 3.25, 0, 0.4, M["pot"], bevel=0.03)
        rnd = random.Random(3)
        for i in range(9):
            a = i / 9 * math.tau
            tilt = rnd.uniform(25, 50)
            mesh(f"leaf{i}", "cone", (0.12, 0.04, 0.38), (-1.05 + 0.06 * math.cos(a), 3.05 + 0.06 * math.sin(a), 0.75),
                 (D(tilt) * math.sin(a), D(-tilt) * math.cos(a) * -1, a), mat=M["leaf"] if i % 2 else M["leaf2"], seg=4)
        mesh("plant_core", "ico", (0.18, 0.18, 0.2), (-1.05, 3.05, 0.55), mat=M["leaf"], seg=1)
        # small succulent on the sill
        mesh("sill_pot", "cyl", (0.07, 0.07, 0.06), (2.25, 3.3, 0.81), mat=M["pot"], seg=8)
        mesh("succ", "ico", (0.08, 0.08, 0.07), (2.25, 3.3, 0.9), mat=M["leaf2"], seg=1)
        # floor lamp next to the sofa
        mesh("lamp_base", "cyl", (0.15, 0.15, 0.015), (2.95, -1.05, 0.015), mat=M["wood_dark"], seg=10)
        mesh("lamp_pole", "cyl", (0.015, 0.015, 0.7), (2.95, -1.05, 0.7), mat=M["wood_dark"], seg=6)
        self.lampshade = mesh("lamp_shade", "cone", (0.25, 0.25, 0.22), (2.95, -1.05, 1.55), mat=M["lampshade"], seg=10)
        self.lampshade.scale = (1, 1, 1)
        # side shelf with books near the door
        box("shelf", -3.45, -3.1, 1.0, 2.4, 0, 0.9, M["wood"], bevel=0.015)
        for i, c in enumerate(("art1", "art2", "art3", "pillow2")):
            box(f"sbook{i}", -3.4, -3.15, 1.1 + i * 0.09, 1.17 + i * 0.09, 0.9, 1.15 - (i % 2) * 0.05, M[c])
        mesh("vase", "cyl", (0.06, 0.06, 0.12), (-3.27, 2.1, 1.02), mat=M["art3"], seg=8)
        # birds (scene 6)
        self.birds = []
        for i in range(3):
            b = empty(f"bird{i}", (0, 30, 0))
            mesh(f"bird_body{i}", "sphere", (0.06, 0.1, 0.055), mat=M["bird"], parent=b, seg=8, rings=5)
            mesh(f"bird_belly{i}", "sphere", (0.045, 0.07, 0.04), (0, 0.01, -0.02), mat=M["bird_belly"], parent=b, seg=8, rings=5)
            mesh(f"bird_beak{i}", "cone", (0.015, 0.015, 0.03), (0, 0.11, 0.01), (D(-90), 0, 0), mat=M["art1"], parent=b, seg=4)
            wings = []
            for s in (-1, 1):
                wp = empty(f"bird_wing{i}{s}", (s * 0.04, 0, 0.02), parent=b)
                mesh(f"bird_wing_m{i}{s}", "cube", (0.08, 0.05, 0.006), (s * 0.08, 0, 0), mat=M["bird"], parent=wp)
                wings.append(wp)
            self.birds.append((b, wings))
        # dust puffs (scene 4)
        self.dust = [mesh(f"dust{i}", "ico", (0.06, 0.06, 0.05), (0, 0, -5), mat=M["dust"], seg=1) for i in range(10)]
        # light shaft (scene 3)
        self.beam = mesh("beam", "cube", (0.32, 0.5, 2.0), (0, 0, -20), mat=M["beam"])
        self.beam.visible_shadow = False
        # Zzz
        self.zs = []
        for i in range(3):
            cu = bpy.data.curves.new(f"z{i}", "FONT")
            cu.body = "z"
            cu.size = 0.07 + i * 0.02
            cu.extrude = 0.01
            cu.align_x = "CENTER"
            ob = link(bpy.data.objects.new(f"z{i}", cu))
            ob.data.materials.append(M["zzz"])
            ob.location = (0, 0, -5)
            self.zs.append(ob)

    def build_lights(self):
        sun = bpy.data.lights.new("sun", "SUN")
        sun.angle = D(2.5)
        self.sun = link(bpy.data.objects.new("sun", sun))
        fill = bpy.data.lights.new("fill", "AREA")
        fill.size = 4.0
        fill.energy = 300
        self.fill = link(bpy.data.objects.new("fill", fill))
        self.fill.location = (0.5, -3.0, 3.0)
        self.fill.rotation_euler = (D(55), 0, 0)
        fill.use_shadow = False
        bounce = bpy.data.lights.new("bounce", "AREA")
        bounce.size = 5
        bounce.energy = 200
        bounce.use_shadow = False
        self.bounce = link(bpy.data.objects.new("bounce", bounce))
        self.bounce.location = (0.5, 1.0, 3.1)
        lamp = bpy.data.lights.new("lamp", "POINT")
        lamp.shadow_soft_size = 0.2
        lamp.energy = 0
        self.lamp = link(bpy.data.objects.new("lamp", lamp))
        self.lamp.location = (2.95, -1.05, 1.45)
        hall = bpy.data.lights.new("hall", "AREA")
        hall.size = 1.2
        hall.energy = 0
        hall.use_shadow = False
        self.hall = link(bpy.data.objects.new("hall", hall))
        self.hall.location = (-4.8, 0, 1.6)
        self.hall.rotation_euler = (0, D(-90), 0)
        spot = bpy.data.lights.new("sunbeam", "SPOT")
        spot.energy = 0
        spot.spot_size = D(22)
        spot.spot_blend = 0.25
        spot.shadow_soft_size = 0.05
        self.spot = link(bpy.data.objects.new("sunbeam", spot))

    def apply_light(self, preset):
        L = CFG["lighting"][preset]
        el, az = D(L["sun_elevation"]), D(L["sun_azimuth"])
        # light travels from outside (+Y) into the room (-Y), slightly toward +X
        d = Vector((math.sin(az) * math.cos(el), -math.cos(az) * math.cos(el), -math.sin(el))).normalized()
        self.sun.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        self.sun.data.color = srgb(L["sun_color"])[:3]
        self.sun.data.energy = L["sun_strength"]
        self.fill.data.color = srgb(L["fill"])[:3]
        self.bounce.data.color = srgb(L["fill"])[:3]
        w = bpy.context.scene.world
        bg = w.node_tree.nodes["Background"]
        bg.inputs["Color"].default_value = srgb(L["ambient"])
        bg.inputs["Strength"].default_value = L["ambient_strength"]
        ramp = self.sky.node_tree.nodes[self.sky["ramp"]]
        ramp.color_ramp.elements[0].color = srgb(L["sky_bottom"])
        ramp.color_ramp.elements[1].color = srgb(L["sky_top"])
        for bm in self.bmats:
            bm.node_tree.nodes[bm["mul"]].inputs[1].default_value = L["city_windows"]
        self.lamp.data.energy = 60 * L["lamp"]
        self.lamp.data.color = srgb("#FFC07A")[:3]
        b = self.lampshade.data.materials[0].node_tree.nodes["Principled BSDF"]
        b.inputs["Emission Color"].default_value = srgb("#FFD9A0")
        b.inputs["Emission Strength"].default_value = 2.5 * L["lamp"]


# ----------------------------------------------------------------------------- scene setup
def setup_render(scale=100, samples=None):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = CFG["video"]["width"]
    sc.render.resolution_y = CFG["video"]["height"]
    sc.render.resolution_percentage = scale
    sc.render.fps = FPS
    sc.eevee.taa_render_samples = samples or CFG["video"]["samples"]
    sc.eevee.use_shadows = True
    sc.eevee.shadow_ray_count = 1
    sc.eevee.shadow_step_count = 1
    sc.eevee.shadow_resolution_scale = 0.5
    sc.eevee.use_raytracing = False   # no visible gain here, ~2x slower on a CPU (Mesa) renderer
    sc.view_settings.view_transform = "AgX"
    try:
        sc.view_settings.look = "AgX - Punchy"
    except Exception:
        pass
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.use_nodes = True


def build():
    global COLL
    bpy.ops.wm.read_factory_settings(use_empty=True)
    MATS.clear()
    COLL = bpy.context.scene.collection
    setup_render()
    world = World()
    cat = Cat(CFG["cat"])
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 30
    cam_data.sensor_fit = "VERTICAL"
    cam_data.sensor_height = 24
    cam_data.clip_start = 0.02
    cam_data.clip_end = 100
    cam = link(bpy.data.objects.new("cam", cam_data))
    bpy.context.scene.camera = cam
    target = empty("cam_target")
    c = cam.constraints.new("TRACK_TO")
    c.target = target
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    return world, cat, cam, target


def clear_anim():
    for ob in bpy.data.objects:
        ob.animation_data_clear()
        if ob.data is not None and hasattr(ob.data, "animation_data_clear"):
            ob.data.animation_data_clear()
    for m in bpy.data.materials:
        if m.node_tree:
            m.node_tree.animation_data_clear()


def keyloc(ob, f, loc=None, rot=None, scale=None):
    if loc is not None:
        ob.location = loc
        ob.keyframe_insert("location", frame=f)
    if rot is not None:
        ob.rotation_euler = rot
        ob.keyframe_insert("rotation_euler", frame=f)
    if scale is not None:
        ob.scale = scale
        ob.keyframe_insert("scale", frame=f)


def cam_key(cam, target, f, pos, look, lens=None):
    keyloc(cam, f, loc=Vector(pos))
    keyloc(target, f, loc=Vector(look))
    if lens:
        cam.data.lens = lens
        cam.data.keyframe_insert("lens", frame=f)


def reset_props(w):
    """Default state of the props for each scene (before scene-specific changes)."""
    w.door.rotation_euler = (0, 0, 0)
    w.glass.location = (1.55, 0.55, 0.44)
    w.glass.rotation_euler = (0, 0, 0)
    w.glass.hide_render = False
    w.puddle.hide_render = True
    w.pillow.location = (3.05, 1.65, 0.66)
    w.pillow.rotation_euler = (0, D(-12), D(-10))
    w.beam.location = (0, 0, -20)
    w.spot.data.energy = 0
    w.hall.data.energy = 0
    for b, _ in w.birds:
        b.location = (0, 30, 0)
    for d in w.dust:
        d.location = (0, 0, -5)
    for z in w.zs:
        z.location = (0, 0, -5)


# ----------------------------------------------------------------------------- the 7 scenes
def scene_door_close(w, cat, cam, tgt, N):
    """8:00 — the front door closes, the cat stares at it, motionless."""
    cat_pos = (-2.35, -0.05, 0)
    cat.root.location = cat_pos
    cat.root.rotation_euler = (0, 0, D(100))   # facing the door (-X)
    # door: open -> slams shut at ~0.7 s
    keyloc(w.door, 1, rot=(0, 0, D(75)))
    keyloc(w.door, T(0.25), rot=(0, 0, D(70)))
    keyloc(w.door, T(0.7), rot=(0, 0, D(0)))
    keyloc(w.door, T(0.8), rot=(0, 0, D(2.5)))
    keyloc(w.door, T(0.9), rot=(0, 0, D(0)))
    w.hall.data.energy = 400
    w.hall.data.keyframe_insert("energy", frame=T(0.55))
    w.hall.data.energy = 0
    w.hall.data.keyframe_insert("energy", frame=T(0.72))
    # cat: sitting, stares. ears flatten at the slam, then a slow head tilt, one slow blink
    for t, ear, tilt, eye, tail in ((0, 0, 0, 1.0, 0), (0.7, 0, 0, 1.0, 0), (0.78, 25, 0, 0.75, 0),
                                    (1.1, 0, 0, 1.0, 0), (1.8, 0, 0, 1.0, 0), (2.3, 0, 12, 1.0, 1),
                                    (2.55, 0, 12, 0.05, 1), (2.7, 0, 12, 1.0, 1), (3.1, 0, 12, 1.0, 1)):
        cat.reset()
        cat.root.location = cat_pos
        cat.root.rotation_euler = (0, 0, D(100))
        cat.sit()
        cat.head.rotation_euler.y = D(tilt)
        for s, e in zip((-1, 1), cat.ears):
            e.rotation_euler = (D(-8 - ear), s * D(22 + ear * 1.6), 0)
        cat.set_eyes(eye, 0)
        cat.tail[-1].rotation_euler.z = D(-20 + 30 * tail)
        cat.key(T(t))
    # camera: low, 3/4 behind the cat looking at the door; tiny shake on the slam
    base = Vector((-1.05, -1.7, 0.58))
    look = Vector((-2.95, 0.05, 0.85))
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p = base + Vector((0.12 * t / 3, 0.1 * t / 3, 0))
        sh = 0.0
        if 0.7 <= t <= 1.0:
            sh = 0.012 * math.sin(t * 90) * (1 - (t - 0.7) / 0.3)
        cam_key(cam, tgt, f, p + Vector((sh, 0, sh)), look, lens=27 if f == 1 else None)


def scene_drama_flop(w, cat, cam, tgt, N):
    """8:05 — he lies down dramatically in front of the door."""
    pos = Vector((-2.5, 0.0, 0))
    yaw = D(95)
    # 0-0.5 s sitting; 0.5-0.9 rises; 0.9-1.25 collapses onto his side; bounce; paw drops at 1.8
    seq = [(0, "sit", 0), (0.45, "sit", 0), (0.8, "stand", 0), (1.15, "flop", 1.1), (1.28, "flop", 0.92),
           (1.4, "flop", 1.0), (1.75, "flop", 1.0), (1.95, "flop2", 1.0), (2.6, "flop2", 1.0)]
    for t, pose, k in seq:
        cat.reset()
        cat.root.location = pos
        cat.root.rotation_euler = (0, 0, yaw)
        if pose == "sit":
            cat.sit()
            cat.set_eyes(1.0, 0.0)
        elif pose == "stand":
            cat.head.rotation_euler = (D(15), 0, 0)
            cat.tail_shape(-70, -8, 4)
            cat.set_eyes(0.5, 0.6)
        else:
            cat.flopped()
            cat.root.rotation_euler = (0, D(90 * k), D(90))
            cat.root.location = pos + Vector((0, 0, 0.135 + 0.25 * (k - 1)))
            cat.set_eyes(0.0, 0.0)
            if pose == "flop":
                cat.legs["fl"].rotation_euler = (D(125), D(-25), 0)   # paw raised "to the forehead"
            else:
                cat.legs["fl"].rotation_euler = (D(70), D(-5), 0)
                cat.tail[0].rotation_euler = (D(-8), 0, 0)
        cat.key(T(t))
    # camera: low on the floor, front-side, slow push in
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p = Vector((-2.45, -2.3, 0.62)).lerp(Vector((-2.5, -2.0, 0.52)), smooth(t / 2.5))
        cam_key(cam, tgt, f, p, Vector((-2.58, 0.0, 0.3)), lens=30 if f == 1 else None)


def scene_sun_nap(w, cat, cam, tgt, N):
    """10:00 — nap in a sunbeam on the sofa."""
    pos = (2.95, 0.25, 0.5)
    w.spot.data.energy = 1400
    w.spot.data.color = srgb("#FFD49A")[:3]
    w.spot.location = (1.2, 3.4, 2.4)
    aim = Vector((2.95, 0.25, 0.5)) - Vector(w.spot.location)
    w.spot.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()
    for f in range(1, N + 1, 3):
        t = (f - 1) / FPS
        cat.reset()
        cat.root.location = pos
        cat.root.rotation_euler = (0, 0, D(118))  # facing the camera
        cat.loaf()
        br = math.sin(t * 2 * math.pi / 1.6)
        cat.body.scale = (1.08 + 0.025 * br, 1.0, 0.95 + 0.03 * br)
        cat.head.rotation_euler = (D(14 + 2 * br), D(-12), 0)
        cat.set_eyes(0.0, 0.0)
        # ear twitch at 1.4 s
        tw = 1.0 if 1.4 < t < 1.55 else 0.0
        cat.ears[1].rotation_euler = (D(-8 - 20 * tw), D(22 + 25 * tw), 0)
        cat.tail[-1].rotation_euler.x = D(15 * math.sin(t * 2))
        cat.key(f)
    # floating Zzz
    for i, z in enumerate(w.zs):
        for f in range(1, N + 1, 2):
            t = (f - 1) / FPS
            ph = ((t + i * 0.55) % 1.65) / 1.65
            z.location = (2.75 - ph * 0.05, 0.12 - ph * 0.22, 0.8 + ph * 0.3)
            z.rotation_euler = (D(90), 0, D(-55 + 20 * math.sin(t * 3 + i)))
            s = math.sin(ph * math.pi)
            z.scale = (s, s, s)
            z.keyframe_insert("location", frame=f)
            z.keyframe_insert("rotation_euler", frame=f)
            z.keyframe_insert("scale", frame=f)
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p = Vector((1.75, -0.55, 0.95)).lerp(Vector((1.95, -0.35, 0.88)), smooth(t / 2.5))
        cam_key(cam, tgt, f, p, Vector((2.95, 0.3, 0.62)), lens=30 if f == 1 else None)


def scene_zoomies(w, cat, cam, tgt, N):
    """12:00 — zoomies: a mad sprint around the coffee table, camera chasing."""
    cx, cy, rx, ry = 1.45, 0.75, 1.3, 1.25
    w.pillow.location = (2.2, -0.1, 0.09)            # already knocked off the sofa
    w.pillow.rotation_euler = (D(80), D(10), D(30))
    dur = N / FPS
    laps = 1.1
    def pos_at(t):
        a = -math.pi / 2 - laps * math.tau * smooth(0.5 * (t / dur) + 0.5 * (t / dur)) if False else \
            -math.pi / 2 - laps * math.tau * (t / dur)
        return Vector((cx + rx * math.cos(a), cy + ry * math.sin(a), 0)), a
    dust_i = 0
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p, a = pos_at(t)
        p2, _ = pos_at(t + 0.02)
        dirv = (p2 - p).normalized()
        yaw = math.atan2(-dirv.x, dirv.y)
        ph = t * 2 * math.pi * 4.2     # gallop cycle
        cat.reset()
        hop = abs(math.sin(ph)) * 0.07
        cat.root.location = p + Vector((0, 0, hop))
        cat.root.rotation_euler = (0, D(-14), yaw)  # leans into the turn
        cat.body.rotation_euler = (D(6 * math.sin(ph)), 0, 0)
        cat.body.scale = (1, 1.0 + 0.1 * math.sin(ph), 1.0 - 0.06 * math.sin(ph))
        sw = 55
        cat.legs["fl"].rotation_euler = (D(sw * math.sin(ph)), 0, 0)
        cat.legs["fr"].rotation_euler = (D(sw * math.sin(ph + 0.4)), 0, 0)
        cat.legs["bl"].rotation_euler = (D(sw * math.sin(ph + math.pi)), 0, 0)
        cat.legs["br"].rotation_euler = (D(sw * math.sin(ph + math.pi + 0.4)), 0, 0)
        cat.head.rotation_euler = (D(10 - 6 * math.sin(ph)), 0, 0)
        for s, e in zip((-1, 1), cat.ears):
            e.rotation_euler = (D(-30), s * D(45), 0)  # ears flattened back
        cat.tail_shape(-15, 6, -4, wave=10, phase=ph)
        cat.set_eyes(1.15, 0.0, pupil=3.0)           # huge round pupils
        cat.key(f)
        # dust puffs every few frames
        if f % 3 == 0:
            d = w.dust[dust_i % len(w.dust)]
            dust_i += 1
            keyloc(d, f - 1, loc=(0, 0, -5), scale=(0.2, 0.2, 0.2))
            keyloc(d, f, loc=p - dirv * 0.25 + Vector((0, 0, 0.04)), scale=(0.4, 0.4, 0.4))
            keyloc(d, f + 8, loc=p - dirv * 0.35 + Vector((0, 0, 0.12)), scale=(1.1, 1.1, 1.0))
            keyloc(d, f + 9, loc=(0, 0, -5))
        # chase camera: behind & outside the loop, lagging
        pc, ac = pos_at(t + 0.3)
        outward = Vector((math.cos(ac), math.sin(ac), 0))
        campos = pc + outward * 1.1 + Vector((0, 0, 0.55))
        campos.x = min(campos.x, 2.45)
        campos.y = max(min(campos.y, 3.2), -3.5)
        campos.x = max(campos.x, -3.2)
        cam_key(cam, tgt, f, campos, p + Vector((0, 0, 0.2)), lens=24 if f == 1 else None)


def scene_glass_push(w, cat, cam, tgt, N):
    """14:00 — slowly pushes the glass off the table while staring at the camera."""
    # cat stands on the coffee table, side-on, facing +Y. camera on -X side.
    top = 0.44
    cpos = Vector((1.48, 0.84, top))
    gx = 1.52
    edge_y = 1.35
    def glass_at(f, y, tilt=0.0, z=top):
        keyloc(w.glass, f, loc=(gx, y, z), rot=(D(tilt), 0, 0))
    def contact(r):   # where the paw tip is for a given reach
        return cpos.y + 0.15 + 0.2 * (1 + 0.25 * r) * math.sin(D(95 * r)) + 0.055
    g0 = 1.16
    glass_at(1, g0)
    glass_at(T(0.95), g0)
    glass_at(T(1.3), contact(0.85))          # first push
    glass_at(T(2.0), contact(0.85))          # pause... stare
    glass_at(T(2.45), contact(1.0))          # second push, right at the edge
    glass_at(T(2.6), 1.36, tilt=-35, z=top - 0.01)
    glass_at(T(2.75), 1.45, tilt=-95, z=top - 0.15)
    glass_at(T(3.0), 1.55, tilt=-170, z=-0.4)  # out of frame
    # cat keys: (t, paw_reach 0..1, head yaw to camera, lid, eyes open)
    keys = [(0.0, 0.0, 0, 0.0, 1.0), (0.35, 0.0, 80, 0.0, 1.0), (0.6, 0.15, 85, 0.6, 1.0), (1.3, 0.85, 85, 0.65, 1.0),
            (1.5, 0.75, 85, 0.68, 1.0), (2.0, 0.8, 88, 0.7, 1.0), (2.45, 1.0, 88, 0.7, 1.0), (2.7, 0.7, 88, 0.6, 1.0),
            (2.95, 0.3, 88, 0.0, 1.0), (3.05, 0.3, 88, 0.0, 1.0), (3.15, 0.3, 88, 0.0, 0.05), (3.3, 0.3, 88, 0.0, 1.0),
            (3.6, 0.3, 88, 0.5, 1.0)]
    for t, reach, yaw, lid, eye in keys:
        cat.reset()
        cat.root.location = cpos
        cat.root.rotation_euler = (0, 0, 0)
        cat.body.rotation_euler = (D(-4), 0, 0)
        cat.head.rotation_euler = (D(-10), D(-8), D(yaw))
        cat.legs["fl"].rotation_euler = (D(lerp(0, 95, reach)), D(lerp(0, -8, reach)), 0)
        cat.legs["fl"].scale = (1, 1, lerp(1.0, 1.25, reach))
        cat.legs["fr"].rotation_euler = (D(-4), 0, 0)
        cat.body.location.z = 0.22 - 0.02 * reach
        cat.tail_shape(-62, -6, 8)
        cat.tail[-1].rotation_euler.z = D(25 * math.sin(t * 3))
        cat.set_eyes(eye, lid, look=(-0.2, 0))
        cat.key(T(t))
    # camera: table height, from -X side, framing cat + table edge at bottom
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p = Vector((-0.05, 1.08, 0.7)).lerp(Vector((0.2, 1.05, 0.68)), smooth(t / 3.5))
        cam_key(cam, tgt, f, p, Vector((1.5, 1.08, 0.74)), lens=30 if f == 1 else None)


def scene_bird_watch(w, cat, cam, tgt, N):
    """16:00 — sitting at the window, watching the birds."""
    cpos = Vector((0.75, 3.28, 0.75))
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        # birds: lazy flight across, outside
        for i, (b, wings) in enumerate(w.birds):
            u = t / 2.5 + i * 0.22
            bx = lerp(-2.5, 4.0, u) if i != 1 else lerp(4.0, -2.0, u)
            bz = 2.05 + 0.25 * math.sin(u * 7 + i) + i * 0.15
            by = 5.2 + i * 0.8
            b.location = (bx, by, bz)
            b.rotation_euler = (0, 0, D(-90 if i != 1 else 90))
            b.keyframe_insert("location", frame=f)
            b.keyframe_insert("rotation_euler", frame=f)
            for s, wp in zip((-1, 1), wings):
                wp.rotation_euler = (0, s * D(-45 * math.sin(t * 22 + i)), 0)
                wp.keyframe_insert("rotation_euler", frame=f)
        # cat follows bird 0 with its head
        b0 = w.birds[0][0].location
        rel = Vector(b0) - (cpos + Vector((0, 0, 0.45)))
        yaw = math.degrees(math.atan2(-rel.x, rel.y))
        cat.reset()
        cat.root.location = cpos
        cat.root.rotation_euler = (0, 0, D(-8))
        cat.sit()
        cat.head.rotation_euler = (D(-36 + 10), D(-yaw * 0.15), D(max(-70, min(70, yaw + 8))))
        for s, e in zip((-1, 1), cat.ears):
            e.rotation_euler = (D(5), s * D(15), 0)   # ears forward, alert
        cat.set_eyes(1.1, 0.0, pupil=2.2)
        # tail tip twitching
        cat.tail[-1].rotation_euler.z = D(-20 + 35 * math.sin(t * 9))
        cat.tail[-2].rotation_euler.z = D(-20 + 15 * math.sin(t * 9 - 0.6))
        # tiny butt wiggle at the end
        if t > 1.7:
            cat.body.rotation_euler.y = D(4 * math.sin(t * 25))
        cat.key(f)
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        p = Vector((-0.55, 1.9, 1.05)).lerp(Vector((-0.4, 2.05, 1.08)), t / 2.5)
        cam_key(cam, tgt, f, p, Vector((0.85, 3.6, 1.35)), lens=27 if f == 1 else None)


def scene_fake_sleep(w, cat, cam, tgt, N):
    """17:30 — the door opens; the cat pretends he slept all day."""
    # evidence of the day
    w.glass.location = (0.82, 0.12, 0.045)
    w.glass.rotation_euler = (D(90), 0, D(30))
    w.puddle.hide_render = False
    w.pillow.location = (2.2, -0.1, 0.09)
    w.pillow.rotation_euler = (D(80), D(10), D(30))
    # shot A 0-0.9 s: cat on the sofa, wide awake, hears the key.
    # shot B 0.9-1.8 s: the door opens.  shot C 1.8 s-end: cat "asleep", one-eye peek.
    A, B = 0.9, 1.8
    keyloc(w.door, 1, rot=(0, 0, 0))
    keyloc(w.door, T(A + 0.1), rot=(0, 0, D(2)))
    keyloc(w.door, T(B - 0.1), rot=(0, 0, D(78)))
    w.hall.data.energy = 0
    w.hall.data.keyframe_insert("energy", frame=T(A + 0.1))
    w.hall.data.energy = 700
    w.hall.data.keyframe_insert("energy", frame=T(A + 0.6))
    pos = Vector((2.92, 0.95, 0.5))
    seq = [(0.0, "sit", 1.0, 0, 0), (0.35, "sit", 1.0, 0, 0), (0.45, "sit", 1.2, 0, 1), (A, "sit", 1.2, 0, 1),
           (A + 0.1, "loaf", 0.0, 0, 0), (B + 0.9, "loaf", 0.0, 0, 0), (B + 1.05, "peek", 0.0, 1, 0),
           (B + 1.45, "peek", 0.0, 1, 0), (B + 1.6, "loaf", 0.0, 0, 0), (4.2, "loaf", 0.0, 0, 0)]
    for t, pose, eye, peek, alert in seq:
        cat.reset()
        cat.root.location = pos
        cat.root.rotation_euler = (0, 0, D(115))
        if pose == "sit":
            cat.sit()
            cat.set_eyes(eye, 0, pupil=1.0 + 1.5 * alert)
            cat.head.rotation_euler.z = D(-25 * alert)   # snaps toward the door
            for s, e in zip((-1, 1), cat.ears):
                e.rotation_euler = (D(5 + 10 * alert), s * D(14), 0)
        elif pose == "mid":
            cat.body.location.z = 0.17
            cat.head.rotation_euler = (D(5), 0, 0)
            cat.set_eyes(0.3)
        else:
            cat.loaf()
            cat.head.rotation_euler = (D(14), D(10), D(-20))
            cat.set_eyes(0.0)
            if peek:   # open only one eye (the one facing the camera)
                cat.irises[0].scale = (1, 1, 0.55)
                cat.closed[0].scale = (0.001, 0.001, 0.001)
                cat.lids[0].rotation_euler = (D(30), 0, 0)
        cat.key(T(t))
    for f in range(1, N + 1):
        t = (f - 1) / FPS
        if t < A:      # sofa, medium
            p = Vector((1.75, 0.15, 0.95)).lerp(Vector((1.85, 0.25, 0.92)), t / A)
            look = Vector((2.92, 0.95, 0.72))
        elif t < B:    # the door
            u = (t - A) / (B - A)
            p = Vector((-0.3, -1.9, 0.95)).lerp(Vector((-0.55, -1.7, 0.95)), u)
            look = Vector((-3.4, 0.1, 1.0))
        else:          # sofa again, slow push-in
            u = smooth((t - B) / 2.2)
            p = Vector((1.65, 0.2, 0.92)).lerp(Vector((1.9, 0.38, 0.86)), u)
            look = Vector((2.92, 0.95, 0.62))
        cam_key(cam, tgt, f, p, look, lens=28 if f == 1 else None)


SCENES = {
    "door_close": scene_door_close, "drama_flop": scene_drama_flop, "sun_nap": scene_sun_nap,
    "zoomies": scene_zoomies, "glass_push": scene_glass_push, "bird_watch": scene_bird_watch,
    "fake_sleep": scene_fake_sleep,
}


def prepare(world, cat, cam, tgt, sc_cfg):
    clear_anim()
    reset_props(world)
    cat.reset()
    world.apply_light(sc_cfg["light"])
    N = round(sc_cfg["duration"] * FPS)
    SCENES[sc_cfg["id"]](world, cat, cam, tgt, N)
    for ob in bpy.data.objects:   # skip lights that are off for the whole scene (saves render time)
        if ob.type == "LIGHT":
            animated = ob.data.animation_data is not None
            ob.hide_render = (ob.data.energy == 0 and not animated)
    s = bpy.context.scene
    s.frame_start, s.frame_end = 1, N
    return N


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", help="comma list of scene numbers to render as stills")
    ap.add_argument("--frame", type=int, help="override preview frame")
    ap.add_argument("--render", help="'all' or comma list of scene numbers")
    ap.add_argument("--scale", type=int, default=100, help="resolution percentage")
    ap.add_argument("--samples", type=int)
    ap.add_argument("--save-blend", action="store_true")
    ap.add_argument("--step", type=int, default=1, help="render every Nth frame (quick animatic)")
    ap.add_argument("--out", default="frames", help="output folder inside build/")
    a = ap.parse_args()
    world, cat, cam, tgt = build()
    setup_render(a.scale, a.samples)
    sc = bpy.context.scene
    scenes = CFG["scenes"]
    if a.preview:
        os.makedirs(os.path.join(HERE, "build", "preview"), exist_ok=True)
        for n in [int(x) for x in a.preview.split(",")]:
            c = scenes[n - 1]
            prepare(world, cat, cam, tgt, c)
            sc.frame_set(a.frame or c["preview_frame"])
            sc.render.filepath = os.path.join(HERE, "build", "preview", f"scene{n}_{c['id']}.png")
            bpy.ops.render.render(write_still=True)
            if a.save_blend:
                bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "build", f"scene{n}.blend"))
    if a.render:
        sel = range(1, len(scenes) + 1) if a.render == "all" else [int(x) for x in a.render.split(",")]
        for n in sel:
            c = scenes[n - 1]
            prepare(world, cat, cam, tgt, c)
            out = os.path.join(HERE, "build", a.out, f"s{n}")
            sc.frame_step = a.step
            os.makedirs(out, exist_ok=True)
            for f in os.listdir(out):
                os.remove(os.path.join(out, f))
            sc.render.filepath = os.path.join(out, "")
            bpy.ops.render.render(animation=True)


if __name__ == "__main__":
    main()
