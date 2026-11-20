"""
"What if rain disappeared for 5 minutes?"  —  vertical short (1080x1920, 30 fps)

Everything is generated procedurally:
  * Blender (bpy module, EEVEE) builds and renders the low-poly 3D city street.
  * ffmpeg assembles the frames and draws all on-screen texts.

Usage (run with a Python that has `bpy` installed, e.g. `pip install bpy==5.2.4`):

    python what_if_rain.py preview            # stills of step 1 and step 3 -> out/preview_*.png
    python what_if_rain.py render             # render all frames            -> out/frames/
    python what_if_rain.py render 0 120       # render only frames 0..119 (to split work)
    python what_if_rain.py compose            # frames + texts -> out/what_if_rain.mp4
    python what_if_rain.py all                # render + compose

Edit the CONFIG block below to change texts, timing, colors, quality.
"""
import math
import os
import random
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# ════════════════════════════════════════════════════════════════════════════
#  CONFIG  — everything you are likely to want to tweak lives here
# ════════════════════════════════════════════════════════════════════════════
CONFIG = {
    # ---- output ----------------------------------------------------------
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "render_scale": 100,          # % of width/height actually rendered by Blender (e.g. 50 = fast draft)
    "samples": 8,                 # EEVEE anti-aliasing samples per frame
    "shadows": False,             # soft sun shadows: ~6x slower on CPU rendering, barely visible under overcast sky
    "planar_reflections": False,  # extra puddle reflection probe: slow; screen-space reflections already mirror
    "out_dir": os.path.join(HERE, "out"),
    "video_name": "what_if_rain.mp4",
    "crf": 18,                    # H.264 quality (lower = better/larger)

    # ---- timeline (seconds) ----------------------------------------------
    # 1) heavy rain  2) freeze + evaporate  3) camera stroll  4) countdown  5) rain returns  6) verdict
    "t_freeze": 3.5,              # drops stop in mid-air
    "t_evap_start": 4.2,          # drops start turning into particles
    "t_evap_dur": 1.6,            # spread of the evaporation
    "t_stroll": 6.2,              # cut to step 3 (puddles / passers-by)
    "t_umbrella_down": 6.5,       # passers-by start lowering umbrellas
    "t_count_start": 11.2,        # countdown 5:00 starts
    "t_count_end": 13.8,          # countdown reaches 0:00
    "t_rain_back": 14.0,          # rain comes back at once
    "t_final": 15.7,              # final wide shot
    "t_verdict": 16.1,            # verdict fades in
    "duration": 19.0,             # total length

    # ---- texts (English, on screen) ---------------------------------------
    "title_lines": ["What if rain disappeared", "for 5 minutes?"],
    "card_label": "THE RAIN",
    "card_value": "0 mm",
    "card_sub": "FOR 5 MINUTES",
    "count_from_seconds": 300,    # 5:00
    "verdict_lines": ["Verdict: almost nothing.", "Clouds don’t disappear —", "only the drops."],
    "t_card_in": 6.8,
    "t_card_out": 13.95,

    # ---- fonts ----------------------------------------------------------
    "font_serif": os.path.join(HERE, "fonts", "PlayfairDisplay-500.ttf"),
    "font_serif_italic": os.path.join(HERE, "fonts", "PlayfairDisplay-Italic.ttf"),
    "font_sans": os.path.join(HERE, "fonts", "Montserrat-600.ttf"),
    "font_sans_bold": os.path.join(HERE, "fonts", "Montserrat-800.ttf"),

    # ---- colors (linear RGB 0..1 for 3D, hex for texts) -------------------
    "sky_top": (0.10, 0.115, 0.14),
    "sky_horizon": (0.40, 0.44, 0.50),
    "fog_color": (0.38, 0.42, 0.48),
    "fog_density": 0.011,
    "cloud_color": (0.075, 0.085, 0.105),
    "cloud_fog_density": 0.0035,
    "rain_color": (0.80, 0.86, 0.95),
    "brick_colors": [(0.30, 0.085, 0.05), (0.24, 0.10, 0.07), (0.36, 0.14, 0.08), (0.20, 0.07, 0.05), (0.33, 0.20, 0.14)],
    "taxi_color": (0.80, 0.52, 0.03),
    "umbrella_colors": [(0.02, 0.02, 0.025), (0.03, 0.06, 0.18), (0.45, 0.04, 0.04), (0.75, 0.55, 0.05),
                        (0.06, 0.20, 0.12), (0.30, 0.30, 0.33)],
    "coat_colors": [(0.05, 0.07, 0.12), (0.35, 0.27, 0.17), (0.20, 0.04, 0.04), (0.12, 0.14, 0.08),
                    (0.18, 0.18, 0.20), (0.03, 0.03, 0.035), (0.30, 0.10, 0.03)],
    "text_color": "white",
    "card_bg": "0x10141c@0.55",
    "accent": "0x9cc8ff",

    # ---- rain ------------------------------------------------------------
    "rain_drops": 9000,
    "rain_speed": 9.0,            # m/s (visual only)
    "rain_length": 0.75,          # streak length in m
    "splashes": 700,
    "seed": 7,
}

C = CONFIG
if os.environ.get("RAIN_SCALE"):   # quick drafts: RAIN_SCALE=50 python what_if_rain.py preview
    C["render_scale"] = int(os.environ["RAIN_SCALE"])
FPS = C["fps"]
NFRAMES = int(round(C["duration"] * FPS))


def F(t):
    """seconds -> frame"""
    return t * FPS


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def smoother(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6 - 15) + 10)


# ════════════════════════════════════════════════════════════════════════════
#  3D SCENE (Blender)
# ════════════════════════════════════════════════════════════════════════════
def build_scene():
    import bpy
    from mathutils import Vector

    rnd = random.Random(C["seed"])
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = C["width"]
    sc.render.resolution_y = C["height"]
    sc.render.resolution_percentage = C["render_scale"]
    sc.render.fps = FPS
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    ee = sc.eevee
    ee.taa_render_samples = C["samples"]
    for attr, val in (("use_raytracing", True), ("use_gtao", True), ("use_shadows", C["shadows"]),
                      ("shadow_ray_count", 1), ("shadow_step_count", 4)):
        try:
            setattr(ee, attr, val)
        except Exception:
            pass
    try:
        ee.ray_tracing_method = "SCREEN"
        ee.ray_tracing_options.resolution_scale = "2"
        ee.ray_tracing_options.screen_trace_quality = 0.5
    except Exception:
        pass
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.exposure = 0.35

    # ---------------------------------------------------------------- world
    world = bpy.data.worlds.new("World")
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = -0.05
    mr.inputs["From Max"].default_value = 0.6
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*C["sky_horizon"], 1)
    mix.inputs["B"].default_value = (*C["sky_top"], 1)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = 1.0
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], mix.inputs["Factor"])
    nt.links.new(mix.outputs["Result"], bg.inputs["Color"])
    nt.links.new(bg.outputs[0], out.inputs[0])

    # ---------------------------------------------------------------- light (soft overcast)
    sun = bpy.data.lights.new("Sun", "SUN")
    sun.energy = 1.4
    sun.angle = math.radians(35)
    sun.color = (0.85, 0.9, 1.0)
    so = bpy.data.objects.new("Sun", sun)
    so.rotation_euler = (math.radians(35), math.radians(-25), math.radians(20))
    sc.collection.objects.link(so)

    # ---------------------------------------------------------------- materials
    def fog_wrap(m, shader_out, density=None):
        n = m.node_tree.nodes
        l = m.node_tree.links
        cam = n.new("ShaderNodeCameraData")
        mul = n.new("ShaderNodeMath"); mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = -(density if density is not None else C["fog_density"])
        ex = n.new("ShaderNodeMath"); ex.operation = "EXPONENT"
        inv = n.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.inputs[0].default_value = 1.0
        em = n.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*C["fog_color"], 1)
        mx = n.new("ShaderNodeMixShader")
        outn = n.new("ShaderNodeOutputMaterial")
        l.new(cam.outputs["View Distance"], mul.inputs[0])
        l.new(mul.outputs[0], ex.inputs[0])
        l.new(ex.outputs[0], inv.inputs[1])
        l.new(inv.outputs[0], mx.inputs[0])
        l.new(shader_out, mx.inputs[1])
        l.new(em.outputs[0], mx.inputs[2])
        l.new(mx.outputs[0], outn.inputs["Surface"])

    def principled(name, color=(0.5, 0.5, 0.5), rough=0.6, metal=0.0, emit=None, emit_strength=0.0,
                   obj_color=False, fog=True, alpha=1.0):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        n = m.node_tree.nodes
        n.clear()
        p = n.new("ShaderNodeBsdfPrincipled")
        p.inputs["Base Color"].default_value = (*color, 1)
        p.inputs["Roughness"].default_value = rough
        p.inputs["Metallic"].default_value = metal
        if emit is not None:
            p.inputs["Emission Color"].default_value = (*emit, 1)
            p.inputs["Emission Strength"].default_value = emit_strength
        if obj_color:
            oi = n.new("ShaderNodeObjectInfo")
            m.node_tree.links.new(oi.outputs["Color"], p.inputs["Base Color"])
        if alpha < 1.0:
            p.inputs["Alpha"].default_value = alpha
            m.surface_render_method = "BLENDED"
        if fog:
            fog_wrap(m, p.outputs[0])
        else:
            o = n.new("ShaderNodeOutputMaterial")
            m.node_tree.links.new(p.outputs[0], o.inputs["Surface"])
        return m, p

    M = {}
    # bricks: brick texture projected on world (x+y, z), tinted by object color
    m = bpy.data.materials.new("Brick")
    m.use_nodes = True
    n = m.node_tree.nodes; l = m.node_tree.links; n.clear()
    p = n.new("ShaderNodeBsdfPrincipled"); p.inputs["Roughness"].default_value = 0.85
    geo = n.new("ShaderNodeNewGeometry")
    sp = n.new("ShaderNodeSeparateXYZ")
    add = n.new("ShaderNodeMath"); add.operation = "ADD"
    cmb = n.new("ShaderNodeCombineXYZ")
    br = n.new("ShaderNodeTexBrick")
    br.inputs["Scale"].default_value = 1.6
    br.inputs["Mortar Size"].default_value = 0.012
    br.inputs["Mortar"].default_value = (0.32, 0.29, 0.27, 1)
    oi = n.new("ShaderNodeObjectInfo")
    dark = n.new("ShaderNodeMix"); dark.data_type = "RGBA"; dark.blend_type = "MULTIPLY"
    dark.inputs["Factor"].default_value = 1.0
    dark.inputs["B"].default_value = (0.75, 0.75, 0.75, 1)
    l.new(geo.outputs["Position"], sp.inputs[0])
    l.new(sp.outputs["X"], add.inputs[0]); l.new(sp.outputs["Y"], add.inputs[1])
    l.new(add.outputs[0], cmb.inputs["X"]); l.new(sp.outputs["Z"], cmb.inputs["Y"])
    l.new(cmb.outputs[0], br.inputs["Vector"])
    l.new(oi.outputs["Color"], br.inputs["Color1"])
    l.new(oi.outputs["Color"], dark.inputs["A"])
    l.new(dark.outputs["Result"], br.inputs["Color2"])
    l.new(br.outputs["Color"], p.inputs["Base Color"])
    fog_wrap(m, p.outputs[0])
    M["brick"] = m

    M["paint"], _ = principled("Paint", obj_color=True, rough=0.55)
    M["paint_gloss"], _ = principled("PaintGloss", obj_color=True, rough=0.18)
    M["window"], _ = principled("Window", (0.035, 0.045, 0.06), rough=0.08, metal=0.3)
    M["window_lit"], _ = principled("WindowLit", (0.6, 0.45, 0.25), emit=(1.0, 0.72, 0.38), emit_strength=1.6)
    M["stone"], _ = principled("Stone", (0.38, 0.37, 0.36), rough=0.8)
    M["sidewalk"], _ = principled("Sidewalk", (0.20, 0.205, 0.21), rough=0.35)
    M["asphalt"], _ = principled("Asphalt", (0.035, 0.038, 0.043), rough=0.22)
    M["paint_line"], _ = principled("Lines", (0.55, 0.55, 0.52), rough=0.4)
    M["glass_tower"], _ = principled("Tower", (0.12, 0.15, 0.19), rough=0.15, metal=0.6)
    M["dark"], _ = principled("Dark", (0.012, 0.012, 0.014), rough=0.45)
    M["chrome"], _ = principled("Chrome", (0.6, 0.6, 0.62), rough=0.2, metal=1.0)
    M["head_light"], _ = principled("HeadLight", (1, 0.95, 0.85), emit=(1.0, 0.92, 0.75), emit_strength=6, fog=False)
    M["tail_light"], _ = principled("TailLight", (0.6, 0.02, 0.02), emit=(1.0, 0.05, 0.03), emit_strength=4, fog=False)
    M["car_glass"], _ = principled("CarGlass", (0.02, 0.025, 0.03), rough=0.05, metal=0.2)
    M["skin"], _ = principled("Skin", obj_color=True, rough=0.6)
    M["foliage"], _ = principled("Foliage", obj_color=True, rough=0.8)
    M["bark"], _ = principled("Bark", (0.07, 0.05, 0.035), rough=0.9)
    M["cloud"], _ = principled("Cloud", C["cloud_color"], rough=1.0, fog=False)
    fog_wrap(M["cloud"], M["cloud"].node_tree.nodes["Principled BSDF"].outputs[0], C["cloud_fog_density"])
    M["lamp"], _ = principled("Lamp", (1, 0.9, 0.7), emit=(1.0, 0.85, 0.6), emit_strength=3, fog=False)

    # puddle: mirror + procedural rain ripples (voronoi rings), strength animated per frame
    m = bpy.data.materials.new("Puddle")
    m.use_nodes = True
    n = m.node_tree.nodes; l = m.node_tree.links; n.clear()
    p = n.new("ShaderNodeBsdfPrincipled")
    p.inputs["Base Color"].default_value = (0.01, 0.012, 0.016, 1)
    p.inputs["Roughness"].default_value = 0.0
    p.inputs["Metallic"].default_value = 0.0
    p.inputs["IOR"].default_value = 1.9
    geo = n.new("ShaderNodeNewGeometry")
    vor = n.new("ShaderNodeTexVoronoi")
    vor.voronoi_dimensions = "4D"
    vor.inputs["Scale"].default_value = 3.5
    vor.inputs["Randomness"].default_value = 1.0
    tval = n.new("ShaderNodeValue"); tval.name = "RippleTime"; tval.label = "RippleTime"
    # ring = sin(d*40 - t) * (1-d)
    m1 = n.new("ShaderNodeMath"); m1.operation = "MULTIPLY"; m1.inputs[1].default_value = 45.0
    m2 = n.new("ShaderNodeMath"); m2.operation = "SUBTRACT"
    m3 = n.new("ShaderNodeMath"); m3.operation = "SINE"
    m4 = n.new("ShaderNodeMath"); m4.operation = "SUBTRACT"; m4.inputs[0].default_value = 1.0
    m5 = n.new("ShaderNodeMath"); m5.operation = "MULTIPLY"
    bump = n.new("ShaderNodeBump"); bump.name = "RippleBump"
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.02
    l.new(geo.outputs["Position"], vor.inputs["Vector"])
    l.new(tval.outputs[0], vor.inputs["W"])
    l.new(vor.outputs["Distance"], m1.inputs[0])
    l.new(m1.outputs[0], m2.inputs[0])
    tv2 = n.new("ShaderNodeMath"); tv2.operation = "MULTIPLY"; tv2.inputs[1].default_value = 25.0
    l.new(tval.outputs[0], tv2.inputs[0])
    l.new(tv2.outputs[0], m2.inputs[1])
    l.new(m2.outputs[0], m3.inputs[0])
    l.new(vor.outputs["Distance"], m4.inputs[1])
    l.new(m3.outputs[0], m5.inputs[0]); l.new(m4.outputs[0], m5.inputs[1])
    l.new(m5.outputs[0], bump.inputs["Height"])
    l.new(bump.outputs[0], p.inputs["Normal"])
    fog_wrap(m, p.outputs[0])
    M["puddle"] = m
    puddle_nodes = (tval, bump)

    # wet asphalt also gets faint ripples via its own bump strength
    # rain streaks / frozen beads / evaporation particles
    m = bpy.data.materials.new("Rain")
    m.use_nodes = True
    n = m.node_tree.nodes; l = m.node_tree.links; n.clear()
    em = n.new("ShaderNodeEmission"); em.inputs["Color"].default_value = (*C["rain_color"], 1)
    em.inputs["Strength"].default_value = 0.9
    em.name = "RainEmit"
    tr = n.new("ShaderNodeBsdfTransparent")
    mx = n.new("ShaderNodeMixShader"); mx.inputs[0].default_value = 0.38; mx.name = "RainMix"
    o = n.new("ShaderNodeOutputMaterial")
    l.new(tr.outputs[0], mx.inputs[1]); l.new(em.outputs[0], mx.inputs[2]); l.new(mx.outputs[0], o.inputs[0])
    m.surface_render_method = "BLENDED"
    M["rain"] = m
    m = M["rain"].copy(); m.name = "Particles"
    m.node_tree.nodes["RainMix"].inputs[0].default_value = 0.85
    m.node_tree.nodes["RainEmit"].inputs["Strength"].default_value = 1.6
    M["particles"] = m
    m = M["rain"].copy(); m.name = "Splash"
    m.node_tree.nodes["RainMix"].inputs[0].default_value = 0.5
    M["splash"] = m
    rain_mats = (M["rain"], M["particles"])

    # ---------------------------------------------------------------- mesh helpers
    coll = sc.collection

    class MB:
        """accumulate many boxes / prisms into one mesh (fast, few objects)"""

        def __init__(self):
            self.v, self.f = [], []

        def box(self, cx, cy, cz, sx, sy, sz, rz=0.0):
            b = len(self.v)
            c, s = math.cos(rz), math.sin(rz)
            for dz in (-0.5, 0.5):
                for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
                    x, y = dx * sx, dy * sy
                    self.v.append((cx + x * c - y * s, cy + x * s + y * c, cz + dz * sz))
            self.f += [(b, b + 3, b + 2, b + 1), (b + 4, b + 5, b + 6, b + 7), (b, b + 1, b + 5, b + 4),
                       (b + 1, b + 2, b + 6, b + 5), (b + 2, b + 3, b + 7, b + 6), (b + 3, b, b + 4, b + 7)]

        def cyl(self, cx, cy, z0, z1, r0, r1, seg=8, cap=True):
            b = len(self.v)
            for z, r in ((z0, r0), (z1, r1)):
                for i in range(seg):
                    a = 2 * math.pi * i / seg
                    self.v.append((cx + r * math.cos(a), cy + r * math.sin(a), z))
            for i in range(seg):
                j = (i + 1) % seg
                self.f.append((b + i, b + j, b + seg + j, b + seg + i))
            if cap:
                self.f.append(tuple(b + seg + i for i in range(seg)))
                self.f.append(tuple(b + i for i in reversed(range(seg))))

        def blob(self, cx, cy, cz, rx, ry, rz, r=None, jitter=0.15):
            # low-poly icosphere-like blob
            r = r or rnd
            t = (1 + 5 ** 0.5) / 2
            vs = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
                  (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
            fs = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2),
                  (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5),
                  (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]
            b = len(self.v)
            for x, y, z in vs:
                ln = math.sqrt(x * x + y * y + z * z)
                k = 1 + r.uniform(-jitter, jitter)
                self.v.append((cx + x / ln * rx * k, cy + y / ln * ry * k, cz + z / ln * rz * k))
            self.f += [(b + a, b + c_, b + d) for a, c_, d in fs]

        def build(self, name, mat, color=None, parent=None):
            me = bpy.data.meshes.new(name)
            me.from_pydata(self.v, [], self.f)
            me.validate()
            me.materials.append(mat)
            ob = bpy.data.objects.new(name, me)
            if color:
                ob.color = (*color, 1)
            if parent:
                ob.parent = parent
            coll.objects.link(ob)
            return ob

    def single_box(name, mat, cx, cy, cz, sx, sy, sz, color=None):
        mb = MB(); mb.box(cx, cy, cz, sx, sy, sz)
        return mb.build(name, mat, color)

    # ---------------------------------------------------------------- street
    Y0, Y1 = -30, 110
    single_box("Road", M["asphalt"], 0, (Y0 + Y1) / 2, -0.05, 10, Y1 - Y0, 0.1)
    for s in (-1, 1):
        single_box("Sidewalk", M["sidewalk"], s * 6.5, (Y0 + Y1) / 2, 0.075, 3, Y1 - Y0, 0.15)
        single_box("Curb", M["stone"], s * 5.05, (Y0 + Y1) / 2, 0.08, 0.12, Y1 - Y0, 0.16)
    single_box("Ground", M["sidewalk"], 0, 60, -0.2, 400, 400, 0.2)
    lines = MB()
    for y in range(Y0, Y1, 6):
        lines.box(0, y, 0.004, 0.14, 2.6, 0.01)
    for i in range(9):          # crosswalk
        lines.box(-4 + i, 22, 0.004, 0.55, 3.0, 0.01)
    lines.build("Lines", M["paint_line"])

    # puddles (irregular low-poly discs)
    pud = MB()
    def puddle(cx, cy, z, rx, ry, seg=14):
        b = len(pud.v)
        pud.v.append((cx, cy, z))
        for i in range(seg):
            a = 2 * math.pi * i / seg
            k = rnd.uniform(0.75, 1.15)
            pud.v.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k, z))
        for i in range(seg):
            pud.f.append((b, b + 1 + i, b + 1 + (i + 1) % seg))
    for cx, cy, rx, ry in [(-2.6, 6.5, 1.9, 2.8), (2.4, 12, 1.4, 2.4), (-1.0, 17, 1.2, 1.8), (2.8, 30, 1.8, 3.2),
                           (-3.0, 38, 1.5, 2.6), (0.5, 3.0, 1.0, 1.4), (-3.4, 0.5, 1.2, 1.8), (3.4, -2, 1.3, 1.9),
                           (1.2, 46, 2.0, 3.0)]:
        puddle(cx, cy, 0.006, rx, ry)
    for cx, cy, rx, ry in [(-6.4, 9.5, 0.9, 1.5), (6.3, 14.5, 1.0, 1.6), (6.6, 4.0, 0.8, 1.2), (-6.6, 18, 0.9, 1.4),
                           (6.4, 22.5, 0.8, 1.3), (-6.2, 1, 0.9, 1.4)]:
        puddle(cx, cy, 0.156, rx, ry)
    pud.build("Puddles", M["puddle"])
    # planar reflection probe just above the road to give crisp mirror puddles
    if C["planar_reflections"]:
        pd = bpy.data.lightprobes.new("PuddleProbe", "PLANE")
        po = bpy.data.objects.new("PuddleProbe", pd)
        po.location = (0, 20, 0.01)
        po.scale = (8, 40, 1)
        coll.objects.link(po)

    # ---------------------------------------------------------------- buildings
    walls, wins, wins_lit, sills, trims = MB(), MB(), MB(), MB(), MB()
    brick_objs = []
    for side in (-1, 1):
        y = -14.0
        while y < 95:
            w = rnd.uniform(6.5, 11)
            h = rnd.choice([9, 12, 12, 15, 18, 21]) + rnd.uniform(-0.5, 0.5)
            depth = 12
            fx = side * 8.0                              # facade plane
            cx = side * (8.0 + depth / 2)
            bmb = MB()
            bmb.box(cx, y + w / 2, h / 2, depth, w - 0.15, h)
            col = rnd.choice(C["brick_colors"])
            ob = bmb.build("Building", M["brick"], color=col)
            brick_objs.append(ob)
            # cornice + ground floor band
            trims.box(fx + side * 0.1, y + w / 2, h + 0.2, 0.6, w - 0.1, 0.4)
            trims.box(fx + side * 0.05, y + w / 2, 3.6, 0.25, w - 0.15, 0.25)
            # shop window ground floor
            wins.box(fx - side * 0.02, y + w / 2, 1.7, 0.1, w - 1.6, 2.4)
            # upper windows grid
            ncol = max(2, int((w - 1.0) / 1.9))
            step = (w - 1.0) / ncol
            fl = 4.4
            while fl + 1.6 < h - 0.4:
                for c in range(ncol):
                    wy = y + 0.5 + step * (c + 0.5)
                    target = wins_lit if rnd.random() < 0.13 else wins
                    target.box(fx - side * 0.03, wy, fl + 0.8, 0.1, 1.0, 1.6)
                    sills.box(fx + side * 0.06, wy, fl - 0.02, 0.2, 1.2, 0.12)
                fl += 3.0
            y += w
    walls_ob = trims.build("Trims", M["stone"])
    wins.build("Windows", M["window"])
    wins_lit.build("WindowsLit", M["window_lit"])
    sills.build("Sills", M["stone"])

    # skyscrapers at the end of the street
    tw = MB()
    for i in range(16):
        x = rnd.uniform(-45, 45)
        yy = rnd.uniform(115, 190)
        if abs(x) < 9 and yy < 125:
            continue
        h = rnd.uniform(40, 110)
        s = rnd.uniform(9, 18)
        tw.box(x, yy, h / 2, s, s, h)
        tw.box(x, yy, h + 3, s * 0.6, s * 0.6, 6)
    tw.build("Towers", M["glass_tower"])

    # ---------------------------------------------------------------- trees + lamps
    trunks, leaves = MB(), MB()
    lampmb, lampglow = MB(), MB()
    for side in (-1, 1):
        for y in np.arange(-6 + (side > 0) * 4, 60, 11):
            x = side * 5.7
            trunks.cyl(x, y, 0.15, 3.2, 0.13, 0.09, seg=6)
            for k in range(3):
                leaves.blob(x + rnd.uniform(-0.5, 0.5), y + rnd.uniform(-0.5, 0.5), 3.6 + k * 0.7 + rnd.uniform(-0.2, 0.2),
                            1.5 - k * 0.3, 1.5 - k * 0.3, 1.2 - k * 0.2, jitter=0.2)
            ly = y + 5.5
            lampmb.cyl(x, ly, 0.15, 5.0, 0.07, 0.05, seg=6)
            lampmb.box(x - side * 0.6, ly, 5.0, 1.3, 0.12, 0.1)
            lampglow.box(x - side * 1.15, ly, 4.92, 0.4, 0.25, 0.06)
    trunks.build("Trunks", M["bark"])
    lv = leaves.build("Leaves", M["foliage"], color=(0.06, 0.12, 0.05))
    lampmb.build("Lamps", M["dark"])
    lampglow.build("LampGlow", M["lamp"])

    # ---------------------------------------------------------------- clouds (heavy, low, dark)
    cl = MB()
    for i in range(260):        # a continuous, lumpy, dark ceiling
        cl.blob(rnd.uniform(-90, 90), rnd.uniform(-60, 260), rnd.uniform(36, 46),
                rnd.uniform(14, 26), rnd.uniform(14, 26), rnd.uniform(4, 9), jitter=0.3)
    clouds = cl.build("Clouds", M["cloud"])

    # ---------------------------------------------------------------- taxis
    taxis = []

    def make_taxi(name):
        root = bpy.data.objects.new(name, None)
        coll.objects.link(root)
        body, glass, wheels, hl, tl, sign = MB(), MB(), MB(), MB(), MB(), MB()
        body.box(0, 0, 0.62, 1.85, 4.6, 0.6)
        body.box(0, -0.15, 1.12, 1.6, 2.4, 0.45)
        glass.box(0, -0.15, 1.13, 1.64, 2.2, 0.36)
        glass.box(0, -0.15, 1.13, 1.5, 2.44, 0.36)
        for wx in (-0.85, 0.85):
            for wy in (-1.45, 1.45):
                b = len(wheels.v)
                seg = 10
                for dx in (-0.12, 0.12):
                    for i in range(seg):
                        a = 2 * math.pi * i / seg
                        wheels.v.append((wx + dx, wy + 0.36 * math.cos(a), 0.36 + 0.36 * math.sin(a)))
                for i in range(seg):
                    j = (i + 1) % seg
                    wheels.f.append((b + i, b + j, b + seg + j, b + seg + i))
                wheels.f.append(tuple(b + i for i in range(seg)))
                wheels.f.append(tuple(b + seg + i for i in reversed(range(seg))))
        for hx in (-0.65, 0.65):
            hl.box(hx, 2.31, 0.72, 0.35, 0.04, 0.14)
            tl.box(hx, -2.31, 0.72, 0.35, 0.04, 0.14)
        sign.box(0, -0.1, 1.45, 0.7, 0.25, 0.2)
        for mb, mat, col in ((body, M["paint_gloss"], C["taxi_color"]), (glass, M["car_glass"], None),
                             (wheels, M["dark"], None), (hl, M["head_light"], None), (tl, M["tail_light"], None),
                             (sign, M["paint"], (0.8, 0.8, 0.78))):
            mb.build(name + "_part", mat, color=col, parent=root)
        taxis.append(root)
        return root

    taxi_specs = [  # x, y0, speed (m/s along -y = toward camera / +y)
        (2.4, 25.0, 0.0), (2.4, 41.0, 0.0), (-2.4, 60.0, -3.2), (-2.4, 18.0, -3.2), (2.4, 0.0, 2.8)]
    for i, (x, y, v) in enumerate(taxi_specs):
        t = make_taxi(f"Taxi{i}")
        t.location = (x, y, 0)
        t.rotation_euler = (0, 0, math.pi if v < 0 else 0)

    # ---------------------------------------------------------------- passers-by
    people = []
    skin_tones = [(0.55, 0.36, 0.25), (0.33, 0.19, 0.11), (0.62, 0.44, 0.33), (0.18, 0.10, 0.06), (0.45, 0.30, 0.20)]
    hair_cols = [(0.02, 0.015, 0.01), (0.08, 0.05, 0.02), (0.2, 0.15, 0.08), (0.12, 0.12, 0.12)]

    def make_person(x, y, facing, idx):
        r = random.Random(idx * 31 + 5)
        root = bpy.data.objects.new(f"Person{idx}", None)
        root.location = (x, y, 0.15)
        root.rotation_euler = (0, 0, facing)
        coll.objects.link(root)
        coat = r.choice(C["coat_colors"])
        h = r.uniform(0.92, 1.06)
        legs = MB()
        legs.box(-0.1, 0, 0.42 * h, 0.13, 0.15, 0.84 * h)
        legs.box(0.1, 0, 0.42 * h, 0.13, 0.15, 0.84 * h)
        legs.build("legs", M["paint"], color=(0.03, 0.03, 0.035), parent=root)
        torso = MB()
        torso.cyl(0, 0, 0.62 * h, 1.5 * h, 0.27, 0.21, seg=8)
        torso.build("torso", M["paint"], color=coat, parent=root)
        # head pivot at the neck so it can tilt up
        neck = bpy.data.objects.new("neck", None)
        neck.parent = root
        neck.location = (0, 0, 1.52 * h)
        coll.objects.link(neck)
        hd = MB(); hd.blob(0, 0, 0.13, 0.12, 0.13, 0.14, r=r, jitter=0.03)
        hd.build("head", M["skin"], color=r.choice(skin_tones), parent=neck)
        hr = MB(); hr.blob(0, -0.03, 0.19, 0.125, 0.13, 0.1, r=r, jitter=0.03)
        hr.build("hair", M["paint"], color=r.choice(hair_cols), parent=neck)
        nose = MB(); nose.box(0, 0.125, 0.12, 0.04, 0.05, 0.05)
        nose.build("nose", M["skin"], color=r.choice(skin_tones), parent=neck)
        # free arm (can be raised, palm out, to "feel" for rain)
        sh = bpy.data.objects.new("shoulder", None)
        sh.parent = root
        sh.location = (0.27, 0, 1.42 * h)
        coll.objects.link(sh)
        arm = MB(); arm.box(0.03, 0, -0.33, 0.1, 0.11, 0.66)
        arm.build("arm", M["paint"], color=coat, parent=sh)
        # umbrella arm + umbrella, pivot at the hand
        arm2 = MB(); arm2.box(-0.26, 0.12, 1.15 * h, 0.1, 0.32, 0.12)
        arm2.box(-0.29, 0.0, 1.30 * h, 0.1, 0.11, 0.3)
        arm2.build("arm2", M["paint"], color=coat, parent=root)
        piv = bpy.data.objects.new("umb_pivot", None)
        piv.parent = root
        piv.location = (-0.24, 0.27, 1.12 * h)
        coll.objects.link(piv)
        shaft = MB(); shaft.cyl(0, 0, -0.05, 1.05, 0.012, 0.012, seg=5)
        shaft.build("shaft", M["dark"], parent=piv)
        cano_root = bpy.data.objects.new("canopy_root", None)
        cano_root.parent = piv
        cano_root.location = (0, 0, 1.05)
        coll.objects.link(cano_root)
        can = MB()
        seg = 8
        b = 0
        can.v.append((0, 0, 0.04))
        for i in range(seg):
            a = 2 * math.pi * i / seg
            can.v.append((0.56 * math.cos(a), 0.56 * math.sin(a), -0.30))
        for i in range(seg):
            can.f.append((0, 1 + (i + 1) % seg, 1 + i))
        # underside
        for i in range(seg):
            can.f.append((0, 1 + i, 1 + (i + 1) % seg))
        can.build("canopy", M["paint"], color=r.choice(C["umbrella_colors"]), parent=cano_root)
        people.append(dict(root=root, neck=neck, shoulder=sh, pivot=piv, canopy=cano_root,
                           delay=r.uniform(0.0, 1.8), back_delay=r.uniform(0.0, 0.35),
                           raises_arm=r.random() < 0.45, look=r.uniform(0.35, 0.6), rnd=r))

    spots = [(6.3, 11.5, math.pi * 0.9), (6.9, 13.0, -math.pi * 0.6), (6.0, 15.7, math.pi * 1.1), (7.0, 16.0, math.pi),
             (6.2, 18.5, -math.pi * 0.4), (6.8, 7.8, math.pi * 0.7), (5.9, 4.5, math.pi * 1.2),
             (-6.2, 12.5, math.pi * 0.2), (-6.9, 15.2, math.pi * 0.4), (-6.0, 8.0, -math.pi * 0.1),
             (-6.7, 19.5, math.pi * 0.25), (-6.1, 25.0, 0.1), (6.5, 26.0, math.pi), (-6.5, 3.0, math.pi * 0.1),
             (6.6, 32.0, math.pi * 1.1), (-6.3, 34.0, 0.3), (-3.0, 22.3, -math.pi / 2), (-1.6, 21.6, math.pi / 2)]
    for i, (x, y, f) in enumerate(spots):
        make_person(x, y, f, i)

    # ---------------------------------------------------------------- rain system (numpy driven)
    N = C["rain_drops"]
    rr = np.random.default_rng(C["seed"])
    rain = dict(
        x=rr.uniform(-10, 10, N), y=rr.uniform(-12, 62, N), z0=rr.uniform(0, 24, N),
        H=24.0, delay=rr.uniform(0, 1, N) ** 0.8 * C["t_evap_dur"],
        pvel=np.stack([rr.normal(0, 0.25, N * 2), rr.normal(0, 0.25, N * 2), rr.uniform(0.5, 1.4, N * 2)], 1),
        poff=rr.normal(0, 0.03, (N * 2, 3)),
    )

    def make_dyn_mesh(name, nverts, faces, mat):
        me = bpy.data.meshes.new(name)
        me.from_pydata([(0, 0, 0)] * nverts, [], faces)
        me.materials.append(mat)
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        return ob

    # drops: triangular prisms (6 verts / 3 side quads)
    faces = []
    for i in range(N):
        b = i * 6
        faces += [(b, b + 1, b + 4, b + 3), (b + 1, b + 2, b + 5, b + 4), (b + 2, b, b + 3, b + 5)]
    drops_ob = make_dyn_mesh("Rain", N * 6, faces, M["rain"])
    # evaporation particles: tetrahedra
    NP = N * 2
    faces = []
    for i in range(NP):
        b = i * 4
        faces += [(b, b + 1, b + 2), (b, b + 3, b + 1), (b + 1, b + 3, b + 2), (b, b + 2, b + 3)]
    parts_ob = make_dyn_mesh("Particles", NP * 4, faces, M["particles"])
    # splashes: small crowns (4 tetra spikes merged as 1 pyramid)
    NS = C["splashes"]
    faces = []
    for i in range(NS):
        b = i * 5
        faces += [(b, b + 1, b + 4), (b + 1, b + 2, b + 4), (b + 2, b + 3, b + 4), (b + 3, b, b + 4)]
    spl_ob = make_dyn_mesh("Splashes", NS * 5, faces, M["splash"])
    sx = rr.uniform(-8, 8, NS)
    splash = dict(x=sx, y=rr.uniform(-8, 50, NS), z=np.where(np.abs(sx) > 5.0, 0.155, 0.005),
                  period=rr.uniform(0.25, 0.7, NS), phase=rr.uniform(0, 1, NS))

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 24
    cam_data.clip_end = 600
    cam = bpy.data.objects.new("Cam", cam_data)
    coll.objects.link(cam)
    sc.camera = cam

    return dict(bpy=bpy, Vector=Vector, sc=sc, cam=cam, people=people, taxis=taxis, taxi_specs=taxi_specs,
                rain=rain, drops_ob=drops_ob, parts_ob=parts_ob, spl_ob=spl_ob, splash=splash,
                puddle_nodes=puddle_nodes, clouds=clouds)


# ════════════════════════════════════════════════════════════════════════════
#  CAMERA — shots (frame ranges) with key poses (time, position, look-at, lens)
# ════════════════════════════════════════════════════════════════════════════
def shots():
    T = C
    return [
        # 1) wide street in heavy rain, then slow push through the frozen drops
        (0, T["t_stroll"], [
            (0.0, (0.8, -9.0, 6.5), (0.0, 30, 4.5), 24),
            (T["t_freeze"], (0.3, -6.0, 5.6), (0.0, 30, 4.0), 24),
            (T["t_stroll"], (-1.4, 1.0, 3.6), (0.5, 25, 3.0), 26)]),
        # 3a) skimming over a mirror-smooth puddle
        (T["t_stroll"], 8.6, [
            (T["t_stroll"], (-2.4, 1.6, 0.30), (-2.3, 14, 1.9), 22),
            (8.6, (-2.6, 4.6, 0.6), (-1.0, 16, 3.4), 22)]),
        # 3b) passers-by lowering umbrellas, looking up, tilt to the dark clouds
        (8.6, T["t_count_start"], [
            (8.6, (3.0, 7.6, 1.0), (6.4, 13.5, 1.9), 24),
            (10.0, (3.3, 8.6, 0.95), (6.4, 14.0, 2.5), 24),
            (T["t_count_start"], (3.4, 9.4, 1.0), (5.4, 16.0, 7.5), 22)]),
        # 4) countdown: slow crane up above the dry street
        (T["t_count_start"], T["t_rain_back"], [
            (T["t_count_start"], (0.0, 4.0, 2.2), (0.0, 30.0, 4.0), 22),
            (T["t_rain_back"], (0.0, 0.5, 7.5), (0.0, 30.0, 3.0), 22)]),
        # 5) rain returns — umbrellas pop open
        (T["t_rain_back"], T["t_final"], [
            (T["t_rain_back"], (3.0, 7.8, 1.1), (6.4, 13.5, 1.9), 24),
            (T["t_final"], (2.8, 7.2, 1.2), (6.4, 13.5, 1.9), 24)]),
        # 6) final wide shot, pulling back
        (T["t_final"], T["duration"] + 1, [
            (T["t_final"], (0.0, -2.0, 4.8), (0.0, 30.0, 4.6), 24),
            (T["duration"], (0.0, -9.0, 7.0), (0.0, 30.0, 4.6), 24)]),
    ]


def catmull(keys, t):
    """interpolate list of (t, value-tuple) with eased catmull-rom"""
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for i in range(len(keys) - 1):
        if keys[i][0] <= t <= keys[i + 1][0]:
            break
    t0, t1 = keys[i][0], keys[i + 1][0]
    u = (t - t0) / (t1 - t0)
    # ease only at shot boundaries
    if len(keys) == 2:
        u = smooth(u) * 0.7 + u * 0.3
    p0 = np.array(keys[max(i - 1, 0)][1], float)
    p1 = np.array(keys[i][1], float)
    p2 = np.array(keys[i + 1][1], float)
    p3 = np.array(keys[min(i + 2, len(keys) - 1)][1], float)
    u2, u3 = u * u, u * u * u
    return 0.5 * ((2 * p1) + (-p0 + p2) * u + (2 * p0 - 5 * p1 + 4 * p2 - p3) * u2 + (-p0 + 3 * p1 - 3 * p2 + p3) * u3)


# ════════════════════════════════════════════════════════════════════════════
#  PER-FRAME UPDATE
# ════════════════════════════════════════════════════════════════════════════
def update(S, frame):
    Vector = S["Vector"]
    t = frame / FPS
    T = C

    # ---- camera
    for (a, b, keys) in shots():
        if a <= t < b:
            ks = [(k[0], (*k[1], *k[2], k[3])) for k in keys]
            v = catmull(ks, t)
            pos, tgt, lens = Vector(v[0:3]), Vector(v[3:6]), v[6]
            # tiny handheld sway, plus a jolt when the rain comes back
            sw = 0.025
            pos += Vector((math.sin(t * 1.3) * sw, 0, math.sin(t * 1.7 + 1) * sw))
            dt = t - T["t_rain_back"]
            if 0 <= dt < 0.5:
                k = (1 - dt / 0.5) * 0.06
                pos += Vector((math.sin(t * 90) * k, 0, math.cos(t * 77) * k))
            S["cam"].location = pos
            S["cam"].rotation_euler = (tgt - pos).to_track_quat("-Z", "Y").to_euler()
            S["cam"].data.lens = lens
            break

    # ---- rain state
    raining = t < T["t_freeze"] or t >= T["t_rain_back"]
    R = S["rain"]
    N = len(R["x"])
    L0 = C["rain_length"]
    w0 = 0.011
    if t < T["t_freeze"]:
        tr = t
    elif t < T["t_rain_back"]:
        tr = T["t_freeze"]
    else:
        tr = t - (T["t_rain_back"] - T["t_freeze"])
    z = R["H"] - ((R["H"] - R["z0"] + tr * C["rain_speed"]) % R["H"])
    x, y = R["x"], R["y"]
    length = np.full(N, L0)
    width = np.full(N, w0)
    vis = np.ones(N)
    if T["t_freeze"] <= t < T["t_rain_back"]:
        # frozen: streaks snap to small beads
        k = smooth((t - T["t_freeze"]) / 0.12)
        length[:] = L0 * (1 - k) + 0.07 * k
        width[:] = w0 * (1 - k) + 0.022 * k
        te = t - T["t_evap_start"] - R["delay"]
        vis = np.clip(1 - te / 0.3, 0, 1)
        # little tremble right before they go
        jitter = np.where((te > -0.25) & (te < 0.3), 0.004, 0.0)
        x = x + np.sin(frame * 2.1 + np.arange(N)) * jitter
    # fade out drops that would brush the lens (they read as big white shards)
    cp = S["cam"].location
    dcam = np.sqrt((x - cp.x) ** 2 + (y - cp.y) ** 2 + (z - cp.z) ** 2)
    vis = vis * np.clip((dcam - 1.3) / 1.2, 0, 1)
    length = length * np.maximum(vis, 0.0)
    width = width * vis
    zc = z
    verts = np.zeros((N, 6, 3))
    for j in range(3):
        a = 2 * math.pi * j / 3
        dx, dy = math.cos(a), math.sin(a)
        verts[:, j, 0] = x + dx * width
        verts[:, j, 1] = y + dy * width
        verts[:, j, 2] = zc + length * 0.5
        verts[:, j + 3, 0] = x + dx * width
        verts[:, j + 3, 1] = y + dy * width
        verts[:, j + 3, 2] = zc - length * 0.5
    if T["t_freeze"] <= t < T["t_rain_back"]:
        # bead shape: pinch top
        verts[:, 0:3, 0] = x[:, None] * 0.3 + verts[:, 0:3, 0] * 0.7
        verts[:, 0:3, 1] = y[:, None] * 0.3 + verts[:, 0:3, 1] * 0.7
    me = S["drops_ob"].data
    me.vertices.foreach_set("co", verts.ravel())
    me.update()

    # ---- evaporation particles (2 per drop, rise + shrink + fade)
    NP = N * 2
    pv = np.zeros((NP, 4, 3))
    if T["t_evap_start"] <= t < T["t_rain_back"]:
        te = np.repeat(t - T["t_evap_start"] - R["delay"], 2)
        life = 0.9
        alive = (te > 0) & (te < life)
        age = np.clip(te, 0, life)
        base = np.stack([np.repeat(x, 2), np.repeat(y, 2), np.repeat(zc, 2)], 1) + R["poff"]
        vel = R["pvel"]
        pos = base + vel * age[:, None] + np.stack([np.zeros(NP), np.zeros(NP), 0.25 * age ** 2], 1)
        size = np.where(alive, 0.018 * np.sin(np.pi * np.clip(age / life, 0, 1)) ** 0.6, 0.0)
        tet = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
        pv = pos[:, None, :] + tet[None, :, :] * size[:, None, None]
    me = S["parts_ob"].data
    me.vertices.foreach_set("co", pv.ravel())
    me.update()

    # ---- splashes on the ground while raining
    SP = S["splash"]
    NS = len(SP["x"])
    sv = np.zeros((NS, 5, 3))
    if raining:
        ph = ((t / SP["period"]) + SP["phase"]) % 1.0
        dur = 0.10 / SP["period"]
        on = ph < dur
        k = np.where(on, ph / np.maximum(dur, 1e-6), 0)
        rad = np.where(on, 0.03 + 0.06 * k, 0.0)
        hh = np.where(on, 0.07 * np.sin(np.pi * k), 0.0)
        cx, cy, cz = SP["x"], SP["y"], SP["z"]
        for j, (dx, dy) in enumerate(((1, 0), (0, 1), (-1, 0), (0, -1))):
            sv[:, j, 0] = cx + dx * rad
            sv[:, j, 1] = cy + dy * rad
            sv[:, j, 2] = cz
        sv[:, 4, 0], sv[:, 4, 1], sv[:, 4, 2] = cx, cy, cz + hh
    me = S["spl_ob"].data
    me.vertices.foreach_set("co", sv.ravel())
    me.update()

    # ---- puddle ripples: strong in rain, fade to mirror-flat after the stop
    tval, bump = S["puddle_nodes"]
    tval.outputs[0].default_value = t * 1.0
    if t < T["t_freeze"]:
        s = 0.35
    elif t < T["t_rain_back"]:
        s = 0.35 * (1 - smooth((t - T["t_freeze"]) / 1.4))
    else:
        s = 0.35 * smooth((t - T["t_rain_back"]) / 0.15)
    bump.inputs["Strength"].default_value = s

    # ---- passers-by: lower umbrellas, look up, some reach out a hand; then reopen
    for p in S["people"]:
        td = t - T["t_umbrella_down"] - p["delay"]
        down = smoother(td / 1.0) if t < T["t_rain_back"] else 0.0
        tb = t - T["t_rain_back"] - p["back_delay"]
        if t >= T["t_rain_back"]:
            down = 1.0 - smoother(tb / 0.35)
        # closing canopy (folds narrow + long) and swing umbrella down beside the leg
        fold = smoother(min(1.0, down * 1.6))
        p["canopy"].scale = (1 - 0.86 * fold, 1 - 0.86 * fold, 1 + 1.6 * fold)
        p["pivot"].rotation_euler = (math.radians(-165) * smoother(max(0.0, down * 1.3 - 0.3)), 0, 0)
        # look up at the sky
        look = smoother((td - 0.4) / 0.9) if t < T["t_rain_back"] else smoother(1 - tb / 0.4) * (t < T["t_rain_back"] + 1)
        look = look if t < T["t_rain_back"] else 0.0 if tb > 0.4 else smoother(1 - tb / 0.4)
        p["neck"].rotation_euler = (-p["look"] * look, 0, 0.15 * math.sin(t * 0.7 + p["delay"] * 3) * look)
        # palm out to check for rain
        if p["raises_arm"]:
            arm = smoother((td - 1.0) / 0.8)
            if t >= T["t_rain_back"]:
                arm = 0.0 if tb > 0.3 else 1 - smoother(tb / 0.3)
            p["shoulder"].rotation_euler = (math.radians(70) * arm, math.radians(-25) * arm, 0)
        else:
            p["shoulder"].rotation_euler = (0, 0, 0)

    # ---- moving taxis
    for ob, (x0, y0, v) in zip(S["taxis"], S["taxi_specs"]):
        if v != 0:
            y = y0 + v * t
            y = (y + 15) % 90 - 15
            ob.location = (x0, y, 0)

    # ---- slow cloud drift
    S["clouds"].location = (t * 0.25, 0, 0)


# ════════════════════════════════════════════════════════════════════════════
#  RENDERING
# ════════════════════════════════════════════════════════════════════════════
def render_frames(frames, outdir, name_fmt="frame_{:04d}.png"):
    import time
    S = build_scene()
    bpy = S["bpy"]
    os.makedirs(outdir, exist_ok=True)
    for f in frames:
        path = os.path.join(outdir, name_fmt.format(f))
        if os.path.exists(path) and name_fmt.startswith("frame_"):
            continue
        t0 = time.time()
        update(S, f)
        S["sc"].frame_current = f
        S["sc"].render.filepath = path
        bpy.ops.render.render(write_still=True)
        print(f"frame {f} done in {time.time() - t0:.1f}s", flush=True)


# ════════════════════════════════════════════════════════════════════════════
#  COMPOSITING WITH FFMPEG (texts, cards, countdown, verdict)
# ════════════════════════════════════════════════════════════════════════════
def spaced(s, sp=" "):
    """letter-spaced small caps look"""
    return sp.join(list(s)).replace(sp + " " + sp, sp * 3)


def compose():
    out = C["out_dir"]
    tdir = os.path.join(out, "txt")
    os.makedirs(tdir, exist_ok=True)
    W, H = C["width"], C["height"]

    def tf(name, text):
        p = os.path.join(tdir, name + ".txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def rounded_alpha(w, h, r, a):
        # alpha mask expression for a rounded rectangle
        return (f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
                f"a='{a}*255*lte(hypot(max(0,max({r}-X,X-({w}-1-{r}))),max(0,max({r}-Y,Y-({h}-1-{r})))),{r})'")

    serif, serif_i = C["font_serif"], C["font_serif_italic"]
    sans, sans_b = C["font_sans"], C["font_sans_bold"]
    col = C["text_color"]
    g = []
    # base: frames -> scale (if drafted smaller) -> subtle vignette
    g.append(f"[0:v]scale={W}:{H}:flags=lanczos,vignette=angle=PI/5,format=yuv420p,format=rgba[base]")

    # ---- title (top, elegant serif, semi-transparent)
    y = 205
    chain = "[base]"
    title = []
    for i, line in enumerate(C["title_lines"]):
        p = tf(f"title{i}", line)
        title.append(f"drawtext=fontfile='{serif}':textfile='{p}':expansion=none:fontsize=70:fontcolor={col}@0.86:"
                     f"x=(w-text_w)/2:y={y + i * 92}:shadowcolor=black@0.55:shadowx=0:shadowy=3")
    g.append(chain + ",".join(title) + "[t1]")

    # ---- info card (left, fade in/out)
    cw, ch = 560, 380
    ti, to = C["t_card_in"], C["t_card_out"]
    p1, p2, p3 = tf("card1", spaced(C["card_label"])), tf("card2", C["card_value"]), tf("card3", spaced(C["card_sub"]))
    g.append(
        f"color=c={C['card_bg']}:s={cw}x{ch}:r={FPS}:d={C['duration']},format=rgba,"
        f"{rounded_alpha(cw, ch, 28, 0.55)},"
        f"drawbox=x=40:y=50:w=6:h=280:color={C['accent']}@0.9:t=fill,"
        f"drawtext=fontfile='{sans}':textfile='{p1}':expansion=none:fontsize=34:fontcolor={col}@0.92:x=72:y=58,"
        f"drawtext=fontfile='{sans_b}':textfile='{p2}':expansion=none:fontsize=150:fontcolor={col}:x=66:y=118,"
        f"drawtext=fontfile='{sans}':textfile='{p3}':expansion=none:fontsize=34:fontcolor={col}@0.92:x=72:y=296,"
        f"fade=t=in:st={ti}:d=0.6:alpha=1,fade=t=out:st={to}:d=0.25:alpha=1[card]")
    g.append(f"[t1][card]overlay=x='60-40*max(0,1-(t-{ti})/0.6)':y=700:enable='between(t,{ti},{to + 0.3})'[t2]")

    # ---- countdown 5:00 -> 0:00 (bottom), accelerated
    c0, c1, N0 = C["t_count_start"], C["t_count_end"], C["count_from_seconds"]
    pw, ph = 420, 170
    rem = f"({N0}*(1-clip((t-{c0})/({c1}-{c0}),0,1)))"
    cnt_text = (f"%{{eif\\:floor({rem}/60)\\:d}}\\:%{{eif\\:floor(mod({rem},60))\\:d\\:2}}")
    g.append(
        f"color=c=0x0d1118@0.6:s={pw}x{ph}:r={FPS}:d={C['duration']},format=rgba,{rounded_alpha(pw, ph, 85, 0.6)},"
        f"drawtext=fontfile='{sans_b}':text='{cnt_text}':fontsize=118:fontcolor={col}:x=(w-text_w)/2:y=(h-text_h)/2+2,"
        f"fade=t=in:st={c0 - 0.3}:d=0.3:alpha=1,fade=t=out:st={C['t_rain_back'] - 0.05}:d=0.2:alpha=1[cnt]")
    g.append(f"[t2][cnt]overlay=x=(W-w)/2:y=1440:enable='between(t,{c0 - 0.3},{C['t_rain_back'] + 0.2})'[t3]")

    # ---- verdict (final shot)
    tv = C["t_verdict"]
    vw, vh = 900, 360
    vt = []
    for i, line in enumerate(C["verdict_lines"]):
        p = tf(f"verdict{i}", line)
        if i == 0:
            vt.append(f"drawtext=fontfile='{sans_b}':textfile='{p}':expansion=none:fontsize=60:fontcolor={col}:"
                      f"x=(w-text_w)/2:y=58")
        else:
            vt.append(f"drawtext=fontfile='{serif_i}':textfile='{p}':expansion=none:fontsize=56:fontcolor={col}@0.95:"
                      f"x=(w-text_w)/2:y={92 + i * 78}")
    g.append(
        f"color=c=0x0d1118@0.6:s={vw}x{vh}:r={FPS}:d={C['duration']},format=rgba,{rounded_alpha(vw, vh, 36, 0.6)},"
        f"drawbox=x=(iw-120)/2:y=150:w=120:h=4:color={C['accent']}@0.9:t=fill,"
        + ",".join(vt) + f",fade=t=in:st={tv}:d=0.7:alpha=1[ver]")
    g.append(f"[t3][ver]overlay=x=(W-w)/2:y='1050+30*max(0,1-(t-{tv})/0.7)':enable='gte(t,{tv})',format=yuv420p[v]")

    fg = os.path.join(out, "filtergraph.txt")
    with open(fg, "w", encoding="utf-8") as fh:
        fh.write(";\n".join(g))
    video = os.path.join(out, C["video_name"])
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-framerate", str(FPS), "-i", os.path.join(out, "frames", "frame_%04d.png"),
           "-filter_complex_script", fg, "-map", "[v]",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(C["crf"]), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-r", str(FPS), "-movflags", "+faststart", "-t", str(C["duration"]), video]
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print("video:", video)


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = C["out_dir"]
    if mode == "preview":
        # step 1 (heavy rain) and step 3 (mirror puddles / passers-by looking up)
        fr = [int(F(1.5)), int(F(7.4)), int(F(10.2))]
        if len(sys.argv) > 2:
            fr = [int(a) for a in sys.argv[2:]]
        render_frames(fr, os.path.join(out, "preview"), "preview_{:04d}.png")
    elif mode in ("render", "all"):
        a = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        b = int(sys.argv[3]) if len(sys.argv) > 3 else NFRAMES
        step = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        render_frames(range(a, min(b, NFRAMES), step), os.path.join(out, "frames"))
        if mode == "all":
            compose()
    elif mode == "compose":
        compose()
