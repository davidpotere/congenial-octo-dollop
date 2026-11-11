#!/usr/bin/env python3
"""
RF-4C Phantom II, 10th Tactical Reconnaissance Wing, RAF Alconbury, 1972.
A multi-color FDM kit for a Bambu Lab P1S + AMS.

The aircraft wears the Southeast Asia (SEA) scheme USAFE Phantoms had then: tan
and two greens on top, light gray underneath. The camouflage is a procedural
pattern in those colors, not a copy of the official T.O. pattern drawing.

The kit is split so each piece prints flat with no supports:

  upper   fuselage top, the whole inner wing, fin, canopy. Prints standing on its
          flat underside. Multi-color: light-gray underside layers, tan / green /
          dark-green camo, and a canopy part.
  lower   fuselage belly and intakes, light gray. Prints upside down on the joint face.
  outer wing panels (L/R) and stabilators (L/R): flat-bottomed, light gray below
          and camo on top. Glue on at the wing fold (12 deg dihedral) and tail
          (23 deg anhedral).
  stand   blue base with an inlaid white legend and a gray post. The post plugs
          into a socket in the belly.

Geometry is approximate, from published dimensions (length 19.17 m, span
11.70 m, 45 deg wing, 12 deg outer-panel dihedral, 23 deg stabilator anhedral).
It is a recognizable desk model, not a scale-accurate master.

    pip install numpy scipy pillow manifold3d scikit-image trimesh
    python build_rf4c.py               # 1:72 (266 mm long)
    python build_rf4c.py --scale 100   # 1:100
"""
from __future__ import annotations

import argparse
import math
import zipfile
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, FillRule, Manifold, OpType
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

HERE = Path(__file__).resolve().parent

# display colors (FS595 approximations) for previews / 3MF hints
COLORS = {
    "underside_lightgray": (0xC8, 0xCB, 0xCB),   # FS36622
    "camo_tan":            (0x9C, 0x82, 0x5E),   # FS30219
    "camo_green":          (0x5C, 0x66, 0x3E),   # FS34102
    "camo_darkgreen":      (0x3A, 0x41, 0x30),   # FS34079
    "canopy":              (0x3A, 0x5A, 0x8C),
    "stand_base":          (0x2A, 0x5A, 0xC0),
    "stand_text":          (0xF0, 0xF0, 0xF0),
    "stand_post":          (0x80, 0x80, 0x80),
}


# =========================================================================== shapes
# All aircraft geometry below is in METRES at full size; it is scaled at the end.

def superellipse(w, zt, zb, n_top=2.2, n_bot=3.0, k=48):
    """Convex (y, z) cross-section: half-width w, top zt, bottom zb."""
    zc, h = (zt + zb) / 2, (zt - zb) / 2
    t = np.linspace(0, 2 * np.pi, k, endpoint=False)
    c, s = np.cos(t), np.sin(t)
    n = np.where(s >= 0, n_top, n_bot)
    y = w * np.sign(c) * np.abs(c) ** (2 / n)
    z = zc + h * np.sign(s) * np.abs(s) ** (2 / n)
    return np.stack([y, z], 1)


def loft_x(stations, **kw):
    """Hull-loft (y,z) sections along x. stations: [(x, w, zt, zb), ...]"""
    secs = []
    for x, w, zt, zb in stations:
        p = superellipse(max(w, 1e-3), zt, zb, **kw)
        secs.append(np.column_stack([np.full(len(p), x), p]))
    return union([Manifold.hull_points(np.vstack([a, b])) for a, b in zip(secs, secs[1:])])


def union(parts):
    parts = [p for p in parts if p is not None and not p.is_empty()]
    return Manifold.batch_boolean(parts, OpType.Add) if len(parts) > 1 else parts[0]


def thickness(xc, t):
    """NACA 4-digit full thickness at chord fraction xc for thickness ratio t."""
    return 10 * t * (0.2969 * np.sqrt(xc) - 0.1260 * xc - 0.3516 * xc ** 2
                     + 0.2843 * xc ** 3 - 0.1036 * xc ** 4)


def flat_bottom_section(x_le, chord, t, tmin, k=40):
    """Airfoil outline in (x, z): flat bottom at z=0, NACA thickness above."""
    xc = (1 - np.cos(np.linspace(0, np.pi, k))) / 2
    z = np.maximum(thickness(xc, t) * chord, tmin)
    top = np.column_stack([x_le + xc * chord, z])
    bot = np.array([[x_le + chord, 0.0], [x_le, 0.0]])
    return np.vstack([top, bot])


def panel(sections):
    """Hull-loft flat-bottom sections given as [(y, x_le, chord, t), ...]."""
    pts = []
    for y, x_le, chord, t, tmin in sections:
        s = flat_bottom_section(x_le, chord, t, tmin)
        pts.append(np.column_stack([s[:, 0], np.full(len(s), y), s[:, 1]]))
    return union([Manifold.hull_points(np.vstack([a, b])) for a, b in zip(pts, pts[1:])])


def fin_shape(sections):
    """Vertical symmetric fin: sections [(z, x_le, chord, t, tmin)], thickness along y."""
    pts = []
    for z, x_le, chord, t, tmin in sections:
        xc = (1 - np.cos(np.linspace(0, np.pi, 40))) / 2
        half = np.maximum(thickness(xc, t) * chord, tmin) / 2
        x = x_le + xc * chord
        ring = np.vstack([np.column_stack([x, half]), np.column_stack([x[::-1], -half[::-1]])])
        pts.append(np.column_stack([ring[:, 0], ring[:, 1], np.full(len(ring), z)]))
    return Manifold.hull_points(np.vstack(pts))


def rot_x(m: Manifold, deg, pivot_y=0.0, pivot_z=0.0):
    return m.translate([0, -pivot_y, -pivot_z]).rotate([deg, 0, 0]).translate([0, pivot_y, pivot_z])


def mirror_y(m: Manifold):
    return m.mirror([0, 1, 0])


# ======================================================================= aircraft

class Phantom:
    """Builds the parts. Units: metres in, millimetres out."""

    def __init__(self, scale_denominator):
        self.k = 1000.0 / scale_denominator          # mm per full-size metre
        self.tmin = 0.8 / self.k                       # thinnest printable edge (0.8 mm), in metres
        self.wing_t = 0.6 / self.k                     # underside color layer thickness, metres

        # --- planform numbers (metres, x aft from nose tip, z=0 wing plane)
        self.fold_y = 4.20
        self.tip_y = 5.85
        self.dihedral = 12.0
        self.anhedral = 23.0
        self.stab_root = (16.55, 0.15, 0.30)  # root starts inside the tail cone; trimmed to fit it   # x_le, y, z of stabilator root leading edge

    # ------------------------------------------------------------------ bodies
    def fuselage(self):
        central = loft_x([
            # x      half-w  top    bottom     long RF-4C camera nose -> spine -> tail cone
            (0.00,  0.03,  0.10,  0.02),
            (0.60,  0.27,  0.36, -0.30),
            (1.60,  0.45,  0.56, -0.60),
            (3.00,  0.58,  0.72, -0.78),
            (4.50,  0.63,  0.84, -0.84),
            (6.00,  0.66,  0.95, -0.86),
            (8.50,  0.70,  1.05, -0.86),
            (11.0,  0.68,  1.00, -0.85),
            (13.5,  0.58,  0.92, -0.75),
            (15.5,  0.46,  0.84, -0.45),
            (17.6,  0.36,  0.72, -0.05),
            (19.17, 0.10,  0.52,  0.30),
        ], n_top=2.3, n_bot=2.8)
        nacelles = loft_x([
            (5.70,  1.20,  0.48, -0.84),
            (9.00,  1.26,  0.48, -0.88),
            (12.5,  1.20,  0.42, -0.86),
            (15.2,  1.04,  0.36, -0.78),
            (17.4,  0.96,  0.26, -0.66),
            (18.55, 0.94,  0.20, -0.60),
        ], n_top=3.5, n_bot=3.5)
        # intake ducts: open fronts with a thin outer wall
        duct = Manifold.cube([1.2, 0.40, 1.16]).translate([5.55, 0.80, -0.78])
        ducts = union([duct, mirror_y(duct)])
        # splitter plates standing off the fuselage side
        sp = Manifold.hull_points(np.array([
            [5.15, 0.70, -0.70], [6.10, 0.70, -0.76], [6.10, 0.70, 0.40], [5.35, 0.70, 0.40],
            [5.15, 0.76, -0.70], [6.10, 0.76, -0.76], [6.10, 0.76, 0.40], [5.35, 0.76, 0.40]]))
        sp_strut = Manifold.cube([0.5, 0.2, 0.9]).translate([5.7, 0.58, -0.6])
        splitters = union([sp, sp_strut, mirror_y(sp), mirror_y(sp_strut)])
        # J79 nozzles with hollow exits
        noz = Manifold.cylinder(0.80, 0.46, 0.46, 48).rotate([0, 90, 0]).translate([18.40, 0.50, -0.18])
        noz_hole = Manifold.cylinder(0.6, 0.37, 0.37, 48).rotate([0, 90, 0]).translate([18.70, 0.50, -0.18])
        nozzles = union([noz, mirror_y(noz)])
        holes = union([noz_hole, mirror_y(noz_hole)])
        body = union([central, nacelles, splitters, nozzles]) - ducts - holes
        return body, union([noz, mirror_y(noz)]).translate([0.0, 0, 0])

    def canopy(self):
        return loft_x([
            (4.45, 0.12, 0.84, 0.60),
            (5.05, 0.40, 1.42, 0.60),
            (5.80, 0.46, 1.74, 0.60),
            (6.70, 0.47, 1.78, 0.60),
            (7.70, 0.47, 1.80, 0.60),
            (8.60, 0.43, 1.62, 0.60),
            (9.60, 0.30, 1.12, 0.60),
        ], n_top=2.0, n_bot=2.0)

    def inner_wing(self):
        tm = self.tmin
        right = panel([
            # y      x_le   chord  t/c
            (0.00,  6.40,  6.90, 0.050, tm),
            (1.20,  7.85,  5.65, 0.055, tm),
            (self.fold_y, 11.40, 2.80, 0.045, tm),
        ])
        return union([right, mirror_y(right)])

    def outer_panel_local(self):
        """Right outer wing panel, flat bottom on z=0, root at y=0 (fold line)."""
        tm = self.tmin
        span = (self.tip_y - self.fold_y) / math.cos(math.radians(self.dihedral))
        return panel([
            (0.00, 11.10, 3.10, 0.040, tm),     # dog-tooth: root LE sticks out ahead of the inner wing
            (span, 13.15, 1.40, 0.035, tm),
        ])

    def outer_panel_placed(self, side=1):
        m = self.outer_panel_local().translate([0, self.fold_y, 0])
        m = rot_x(m, self.dihedral, pivot_y=self.fold_y)
        return m if side > 0 else mirror_y(m)

    def stab_local(self):
        tm = self.tmin
        x_le, _, _ = self.stab_root
        span = 2.35 / math.cos(math.radians(self.anhedral))
        return panel([
            (0.00, x_le, 2.75, 0.045, tm),
            (span, x_le + 2.25, 0.95, 0.04, tm),
        ])

    def stab_placed(self, side=1):
        x_le, y0, z0 = self.stab_root
        m = self.stab_local().translate([0, y0, z0])
        m = rot_x(m, -self.anhedral, pivot_y=y0, pivot_z=z0)
        return m if side > 0 else mirror_y(m)

    def fin(self):
        tm = self.tmin
        return fin_shape([
            (0.60, 14.10, 4.30, 0.06, tm),
            (3.25, 17.30, 1.15, 0.05, tm),
        ])


# ======================================================================= camouflage

class Camo:
    """Three-color disruptive pattern as a smooth 3D field, so patches wrap
    continuously over fuselage, wings and tail."""

    def __init__(self, bounds, seed=1972, patch_m=1.7):
        lo, hi = np.array(bounds[:3]), np.array(bounds[3:])
        step = 0.25
        axes = [np.arange(a - 1, b + 1, step) for a, b in zip(lo, hi)]
        rng = np.random.default_rng(seed)
        shape = tuple(len(a) for a in axes)
        sig = patch_m / step / 2.5
        sig = (sig * 1.35, sig, sig * 0.8)               # patches stretched fore-aft a little
        f1 = ndimage.gaussian_filter(rng.standard_normal(shape), sig, mode="wrap")
        f2 = ndimage.gaussian_filter(rng.standard_normal(shape), sig, mode="wrap")
        self.f1 = f1 - np.quantile(f1, 2 / 3)            # > 0 on ~1/3 of space: tan
        self.f2 = f2 - np.quantile(f2, 1 / 2)            # splits the rest between the greens
        self.origin, self.step = [a[0] for a in axes], step
        self.bounds = list(lo - 0.2) + list(hi + 0.2)

    def _sampler(self, f):
        x0, y0, z0 = self.origin
        inv = 1.0 / self.step
        nx, ny, nz = (d - 2 for d in f.shape)
        g = f.tolist()

        def sample(x, y, z):
            fx, fy, fz = (x - x0) * inv, (y - y0) * inv, (z - z0) * inv
            i = min(max(int(fx), 0), nx); j = min(max(int(fy), 0), ny); k = min(max(int(fz), 0), nz)
            tx, ty, tz = fx - i, fy - j, fz - k
            a, b = g[i], g[i + 1]
            c00 = a[j][k] * (1 - tz) + a[j][k + 1] * tz
            c01 = a[j + 1][k] * (1 - tz) + a[j + 1][k + 1] * tz
            c10 = b[j][k] * (1 - tz) + b[j][k + 1] * tz
            c11 = b[j + 1][k] * (1 - tz) + b[j + 1][k + 1] * tz
            return (c00 * (1 - ty) + c01 * ty) * (1 - tx) + (c10 * (1 - ty) + c11 * ty) * tx
        return sample

    def regions(self, edge=0.1):
        tan = Manifold.level_set(self._sampler(self.f1), self.bounds, edge)
        grn = Manifold.level_set(self._sampler(self.f2), self.bounds, edge)
        return tan, grn

    @staticmethod
    def split(solid, tan, grn):
        t = solid ^ tan
        rest = solid - tan
        return {"camo_tan": t, "camo_green": rest ^ grn, "camo_darkgreen": rest - grn}


# ========================================================================== output

def mesh_arrays(m: Manifold):
    mesh = m.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3].astype(np.float64), np.asarray(mesh.tri_verts)


def write_stl(path, m: Manifold):
    if m.is_empty():
        return
    V, F = mesh_arrays(m)
    tri = V[F]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    rec = np.zeros(len(F), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = nrm, tri
    with open(path, "wb") as fh:
        fh.write(b"RF-4C Phantom II".ljust(80, b" "))
        fh.write(np.uint32(len(F)).tobytes())
        fh.write(rec.tobytes())


def write_3mf(path, objects):
    """objects: [(object_name, {part_name: Manifold}, (dx, dy))] -> one build item per object."""
    names = list(COLORS)
    mats = "".join(f'<base name="{n}" displaycolor="#{r:02X}{g:02X}{b:02X}FF"/>'
                   for n, (r, g, b) in COLORS.items())
    res, items = [], []
    oid = 2
    for obj_name, parts, (dx, dy) in objects:
        comp_ids = []
        for pname, m in parts.items():
            if m is None or m.is_empty():
                continue
            V, F = mesh_arrays(m)
            vs = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in V)
            ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
            res.append(f'<object id="{oid}" type="model" name="{pname}" pid="1" '
                       f'pindex="{names.index(pname)}"><mesh><vertices>{vs}</vertices>'
                       f'<triangles>{ts}</triangles></mesh></object>')
            comp_ids.append(oid)
            oid += 1
        comps = "".join(f'<component objectid="{i}"/>' for i in comp_ids)
        res.append(f'<object id="{oid}" type="model" name="{obj_name}"><components>{comps}</components></object>')
        items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 {dx:.3f} {dy:.3f} 0"/>')
        oid += 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Title">RF-4C Phantom II, 10th TRW, RAF Alconbury 1972</metadata>'
             f'<resources><basematerials id="1">{mats}</basematerials>{"".join(res)}</resources>'
             f'<build>{"".join(items)}</build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def text_section(text, height_mm, font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
    """Rasterize text and trace it into a CrossSection (mm, baseline-centred at origin)."""
    from skimage import measure
    px = 20  # pixels per mm
    font = ImageFont.truetype(font_path, int(height_mm * px))
    l, t, r, b = font.getbbox(text)
    img = Image.new("L", (r - l + 40, b - t + 40), 0)
    ImageDraw.Draw(img).text((20 - l, 20 - t), text, fill=255, font=font)
    a = np.asarray(img, dtype=float) / 255
    polys = []
    for c in measure.find_contours(np.pad(a, 1), 0.5):
        c = measure.approximate_polygon(c, 0.6)
        if len(c) >= 4:
            polys.append(np.column_stack([c[:, 1] / px, -c[:, 0] / px]))
    cs = CrossSection(polys, FillRule.EvenOdd)
    x0, y0, x1, y1 = cs.bounds()
    return cs.translate([-(x0 + x1) / 2, -(y0 + y1) / 2])


# ============================================================================ main

def build(args):
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    P = Phantom(args.scale)
    k = P.k

    print("1/6 airframe")
    body, nozzle_zone = P.fuselage()
    canopy = P.canopy()
    wing = P.inner_wing()
    fin = P.fin()
    airframe = union([body, canopy, wing, fin])

    # stand socket + glue pins (sizes in mm, converted to metres)
    socket = Manifold.cylinder(9.0 / k, 3.25 / k, 3.25 / k, 32).translate([10.6, 0, -0.95])
    pins = union([Manifold.cylinder(6.0 / k, 0.98 / k, 0.98 / k, 20).translate([x, 0, -3.0 / k])
                  for x in (3.6, 7.8, 13.6)])

    upper = airframe.trim_by_plane([0, 0, 1], 0.0) - pins
    lower = (body.trim_by_plane([0, 0, -1], 0.0) - pins - socket)

    print("2/6 camouflage")
    cam = Camo(bounds=(-0.5, -6.2, -1.3, 19.8, 6.2, 3.6), seed=args.seed)
    tan, grn = cam.regions(edge=args.camo_edge)

    # gray underside layer on the wings only (inside the fuselage it is the glue face)
    under = (upper ^ Manifold.cube([40, 20, P.wing_t]).translate([-10, -10, 0])) - body
    canopy_part = (upper ^ canopy) - body - under
    # canopy framing: windscreen bow, fixed center section between cockpits, rear bow
    frames = union([Manifold.cube([w, 2.0, 3.0]).translate([x, -1.0, 0.2])
                    for x, w in ((5.02, 0.14), (6.55, 0.42), (8.50, 0.14))])
    frames = canopy_part ^ frames
    canopy_part = canopy_part - frames
    metal = (upper ^ nozzle_zone.trim_by_plane([1, 0, 0], 18.55)) - under
    camo_solid = upper - under - canopy_part - metal
    upper_parts = {"underside_lightgray": union([under, metal])}
    upper_parts.update(Camo.split(camo_solid, tan, grn))
    upper_parts["canopy"] = canopy_part

    def two_tone(local):
        """Flat-bottom panel: light-gray bottom layers + camo top, colored in place."""
        bottom = local ^ Manifold.cube([40, 20, P.wing_t]).translate([-10, -10, 0])
        top = local - bottom
        return bottom, top

    print("3/6 wing panels and stabilators")
    small = {}
    for side, tag in ((1, "R"), (-1, "L")):
        for kind, local_fn, place in (
            ("outer_wing", P.outer_panel_local, lambda s: (P.fold_y, P.dihedral, 0.0)),
            ("stabilator", P.stab_local, lambda s: (P.stab_root[1], -P.anhedral, P.stab_root[2])),
        ):
            local = local_fn()
            y0, ang, z0 = place(side)
            if kind == "stabilator":   # trim the root so it seats against the tail cone
                placed = rot_x(local.translate([0, y0, z0]), ang, pivot_y=y0, pivot_z=z0) - body
                local = rot_x(placed, -ang, pivot_y=y0, pivot_z=z0).translate([0, -y0, -z0])
                local = max(local.decompose(), key=lambda m: m.volume())
            bottom, top = two_tone(local)
            # move the top into the assembled pose to sample the same camo field
            to_world = lambda m: (lambda w: w if side > 0 else mirror_y(w))(
                rot_x(m.translate([0, y0, z0]), ang, pivot_y=y0, pivot_z=z0))
            world_parts = Camo.split(to_world(top), tan, grn)
            back = lambda w: (lambda m: rot_x(m, -ang, pivot_y=y0, pivot_z=z0).translate([0, -y0, -z0]))(
                w if side > 0 else mirror_y(w))
            parts = {"underside_lightgray": bottom}
            parts.update({n: back(m) for n, m in world_parts.items()})
            if side < 0:  # left side prints as the mirror image
                parts = {n: mirror_y(m) for n, m in parts.items()}
            small[f"{kind}_{tag}"] = parts

    print("4/6 stand")
    base_cs = CrossSection.circle(1.0, 96).scale([45, 30])
    base = base_cs.extrude(6.0)
    legend1 = text_section("RF-4C PHANTOM II", 4.6)
    legend2 = text_section("10th TRW  -  RAF ALCONBURY  -  1972", 3.0)
    inlay = union([legend1.translate([0, 6.5]).extrude(0.6).translate([0, 0, 5.4]),
                   legend2.translate([0, -1.0]).extrude(0.6).translate([0, 0, 5.4])])
    post = Manifold.cylinder(72.0, 3.05, 3.05, 32).translate([0, -16, 6.0]) \
        + Manifold.cylinder(1.0, 5.0, 3.05, 32).translate([0, -16, 6.0])      # small fillet at the foot
    stand = {"stand_base": base - inlay, "stand_text": inlay ^ base,
             "stand_post": post}

    print("5/6 scale, check, lay out")
    S = lambda m: m.scale([k, k, k])   # noqa: E731
    upper_mm = {n: S(m) for n, m in upper_parts.items()}
    # lower half: camo comes down the fuselage sides to a demarcation line, gray belly below
    demarc = -args.demarcation
    belly = (lower ^ Manifold.cube([40, 20, 10]).translate([-10, -10, demarc - 10])) \
        + (lower ^ nozzle_zone.trim_by_plane([1, 0, 0], 18.55))
    lower_parts = {"underside_lightgray": belly}
    lower_parts.update(Camo.split(lower - belly, tan, grn))
    flip = lambda m: S(m).rotate([180, 0, 0])   # noqa: E731
    lz = flip(lower).bounding_box()[2]
    lower_mm = {n: flip(m).translate([0, 0, -lz]) for n, m in lower_parts.items()}
    small_mm = {n: {p: S(m) for p, m in parts.items()} for n, parts in small.items()}

    import trimesh
    def check(name, m):
        if m.is_empty():
            print(f"    {name:34s} (no area of this color)")
            return
        V, F = mesh_arrays(m)
        tm = trimesh.Trimesh(V, F, process=False)
        bb = m.bounding_box()
        print(f"    {name:34s} {len(F):>7,} tris  watertight={tm.is_watertight}  "
              f"{bb[3]-bb[0]:6.1f} x {bb[4]-bb[1]:6.1f} x {bb[5]-bb[2]:5.1f} mm  {m.volume()/1000:6.2f} cm3")
    for n, m in upper_mm.items():
        check("upper/" + n, m)
    for n, m in lower_mm.items():
        check("lower/" + n, m)
    for n, parts in small_mm.items():
        for p, m in parts.items():
            check(f"{n}/{p}", m)
    for n, m in stand.items():
        check("stand/" + n, m)

    # bed fit: find a rotation that puts the upper half inside the P1S bed
    up_all = union(list(upper_mm.values()))
    V, _ = mesh_arrays(up_all)
    xy = V[:, :2]
    best = None
    for deg in np.arange(0, 90.1, 0.5):
        a = math.radians(deg)
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        p = xy @ R.T
        size = p.max(0) - p.min(0)
        if best is None or size.max() < best[1].max():
            best = (deg, size)
    print(f"    upper half fits the bed rotated {best[0]:.1f} deg: {best[1][0]:.0f} x {best[1][1]:.0f} mm "
          f"({'OK' if best[1].max() <= 250 else 'TOO BIG for 256 mm bed'})")
    rot = best[0]

    print("6/6 write")
    def centered(m, deg=0.0):
        m = m.rotate([0, 0, deg])
        b = m.bounding_box()
        return m.translate([-(b[0] + b[3]) / 2, -(b[1] + b[4]) / 2, -b[2]])
    # plate 1: upper half alone, rotated to fit
    ref = centered(up_all, rot)
    rb = up_all.rotate([0, 0, rot]).bounding_box()
    shift = [-(rb[0] + rb[3]) / 2, -(rb[1] + rb[4]) / 2, 0]
    up_plate = {n: m.rotate([0, 0, rot]).translate(shift) for n, m in upper_mm.items()}
    write_3mf(out / "plate1_upper_multicolor.3mf", [("RF-4C upper", up_plate, (128, 128))])

    # plate 2: lower half + 4 small parts
    lower_all = union(list(lower_mm.values())).rotate([0, 0, 45])
    lb = lower_all.bounding_box()
    lsh = [-(lb[0] + lb[3]) / 2, -(lb[1] + lb[4]) / 2, 0]
    objs = [("RF-4C lower", {n: m.rotate([0, 0, 45]).translate(lsh) for n, m in lower_mm.items()}, (128, 128))]
    spots = {"outer_wing_R": (55, 195), "outer_wing_L": (200, 60),
             "stabilator_R": (105, 220), "stabilator_L": (150, 36)}
    for n, parts in small_mm.items():
        allp = union(list(parts.values()))
        b = allp.bounding_box()
        sh = [-(b[0] + b[3]) / 2, -(b[1] + b[4]) / 2, -b[2]]
        placed = {p: m.translate(sh) for p, m in parts.items()}
        objs.append((f"RF-4C {n.replace('_', ' ')}", placed, spots[n]))
    write_3mf(out / "plate2_lower_and_panels.3mf", objs)

    # plate 3: stand
    write_3mf(out / "plate3_stand.3mf", [("Display stand", stand, (128, 128))])

    stl = out / "stl"
    stl.mkdir(exist_ok=True)
    for n, m in up_plate.items():
        write_stl(stl / f"upper_{n}.stl", m)
    for n, m in lower_mm.items():
        write_stl(stl / f"lower_{n}.stl", m)
    for n, parts in small_mm.items():
        for p, m in parts.items():
            write_stl(stl / f"{n}_{p}.stl", m)
    for n, m in stand.items():
        write_stl(stl / f"{n}.stl", m)

    # assembled model (display colors) for previews
    asm = dict(upper_parts)
    for n, m in lower_parts.items():
        asm[n] = union([asm[n], m])
    for side in (1, -1):
        tag = "R" if side > 0 else "L"
        for kind, place in (("outer_wing", (P.fold_y, P.dihedral, 0.0)),
                            ("stabilator", (P.stab_root[1], -P.anhedral, P.stab_root[2]))):
            y0, ang, z0 = place
            for p, m in small[f"{kind}_{tag}"].items():
                w = m if side > 0 else mirror_y(m)
                w = rot_x(w.translate([0, y0, z0]), ang, pivot_y=y0, pivot_z=z0)
                w = w if side > 0 else mirror_y(w)
                asm[p] = union([asm[p], w])
    np.savez_compressed(HERE / ".cache_assembled.npz",
                        **{f"{n}_V": mesh_arrays(S(m))[0] for n, m in asm.items()},
                        **{f"{n}_F": mesh_arrays(S(m))[1] for n, m in asm.items()})
    print(f"    files in {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", type=float, default=72, help="scale denominator: 72 -> 1:72")
    ap.add_argument("--seed", type=int, default=1972, help="camouflage pattern seed")
    ap.add_argument("--camo-edge", type=float, default=0.1,
                    help="camo boundary resolution in full-size metres")
    ap.add_argument("--demarcation", type=float, default=0.50,
                    help="full-size metres below the wing plane where side camo meets the gray belly")
    ap.add_argument("--out", type=Path, default=HERE / "output")
    build(ap.parse_args())


if __name__ == "__main__":
    main()
