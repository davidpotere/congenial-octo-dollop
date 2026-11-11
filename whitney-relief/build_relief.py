#!/usr/bin/env python3
"""
Mt Whitney exaggerated-relief model for a Bambu Lab P1S + AMS.

Builds four interlocking, watertight bodies that slice as one multi-color print:

    slot 1  gray   rock (also the base/plinth)
    slot 2  white  snow
    slot 3  green  trees
    slot 4  blue   lakes

Data sources (both free, no account needed):
  * Elevation: AWS Open Data "Terrain Tiles" (terrarium PNG, USGS 3DEP in the US)
  * Lakes + permanent ice: USGS National Hydrography Dataset (NHD) waterbodies

Snow and trees are modelled from elevation, slope and aspect (snowline,
treeline), so they're stylized rather than mapped. Tweak them with the flags.

Usage:
    pip install numpy scipy pillow trimesh
    python build_relief.py                     # defaults: 200 mm wide, 1.75x vertical
    python build_relief.py --width 180 --exaggeration 2.0 --snowline 3500
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent

# Default frame: Whitney's summit plateau and east face, Mt Russell's south side,
# Guitar / Hitchcock / Crabtree lakes on the west, and the Whitney Portal trail
# (Mirror, Consultation, Boy Scout lakes) on the east.
DEFAULT_BBOX = (-118.345, 36.540, -118.232, 36.612)  # west, south, east, north
WHITNEY = (-118.2923, 36.5785)

ROCK, SNOW, TREE, WATER = 0, 1, 2, 3
NAMES = {ROCK: "rock_gray", SNOW: "snow_white", TREE: "trees_green", WATER: "water_blue"}
RGB = {ROCK: (128, 128, 128), SNOW: (245, 245, 245), TREE: (40, 120, 50), WATER: (40, 90, 200)}
SLOT = {ROCK: 1, SNOW: 2, TREE: 3, WATER: 4}

UA = {"User-Agent": "relief-model-builder/1.0"}


# --------------------------------------------------------------------------- data

def fetch(url: str, cache: Path) -> bytes:
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return cache.read_bytes()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    cache.write_bytes(data)
    return data


def lonlat_to_pixel(lon, lat, z):
    """Global Web Mercator pixel coordinates (256 px tiles) at zoom z."""
    n = 256 * 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def load_dem(bbox, zoom, cache_dir: Path):
    """Mosaic terrarium tiles covering bbox. Returns (elev array, px0, py0)."""
    w, s, e, n = bbox
    x0, y0 = lonlat_to_pixel(w, n, zoom)
    x1, y1 = lonlat_to_pixel(e, s, zoom)
    tx0, ty0 = int(x0 // 256) - 1, int(y0 // 256) - 1
    tx1, ty1 = int(x1 // 256) + 1, int(y1 // 256) + 1
    rows = []
    for ty in range(ty0, ty1 + 1):
        row = []
        for tx in range(tx0, tx1 + 1):
            url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{zoom}/{tx}/{ty}.png"
            png = fetch(url, cache_dir / f"terrarium_{zoom}_{tx}_{ty}.png")
            a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float64)
            row.append(a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768)
        rows.append(np.hstack(row))
    return np.vstack(rows), tx0 * 256, ty0 * 256


def load_nhd(bbox, cache_dir: Path):
    """NHD large-scale waterbodies (lakes, ponds, ice masses) intersecting bbox."""
    w, s, e, n = bbox
    q = {
        "f": "geojson", "where": "1=1", "outFields": "gnis_name,ftype,areasqkm",
        "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
        "inSR": "4326", "outSR": "4326", "spatialRel": "esriSpatialRelIntersects",
    }
    url = ("https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/12/query?"
           + urllib.parse.urlencode(q))
    key = "nhd_" + "_".join(f"{v:.4f}" for v in bbox) + ".geojson"
    try:
        return json.loads(fetch(url, cache_dir / key))
    except Exception as ex:  # network hiccup: carry on without lakes
        print(f"  ! could not load NHD water ({ex}); continuing without lakes")
        return {"features": []}


# --------------------------------------------------------------------- terrain

def build_grid(args):
    w, s, e, n = args.bbox
    lat_c = (s + n) / 2
    m_per_deg_lon = 111_320 * math.cos(math.radians(lat_c))
    m_per_deg_lat = 110_574
    width_m = (e - w) * m_per_deg_lon
    height_m = (n - s) * m_per_deg_lat
    scale = args.width / width_m  # mm per metre (horizontal)
    nx = int(round(args.width / args.cell))
    ny = int(round(height_m * scale / args.cell))
    cell = args.width / nx
    # vertex lon/lat, row 0 = north
    jj = np.arange(nx + 1)
    ii = np.arange(ny + 1)
    lon = w + jj * cell / scale / m_per_deg_lon
    lat = n - ii * cell / scale / m_per_deg_lat
    LON, LAT = np.meshgrid(lon, lat)
    return dict(nx=nx, ny=ny, cell=cell, scale=scale, LON=LON, LAT=LAT,
                width_m=width_m, height_m=height_m, m_per_deg_lon=m_per_deg_lon,
                m_per_deg_lat=m_per_deg_lat)


def sample_dem(dem, px0, py0, zoom, LON, LAT):
    x, y = lonlat_to_pixel(LON, LAT, zoom)
    return ndimage.map_coordinates(dem, [y - py0 - 0.5, x - px0 - 0.5], order=3, mode="nearest")


def rasterize(features, grid, bbox, ftypes, supersample=4):
    """Fraction of each cell covered by polygons whose FTYPE is in ftypes."""
    w, s, e, n = bbox
    nx, ny = grid["nx"], grid["ny"]
    W, H = nx * supersample, ny * supersample
    sx = W / ((e - w) * grid["m_per_deg_lon"])
    sy = H / ((n - s) * grid["m_per_deg_lat"])
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    def pts(ring):
        return [((lo - w) * grid["m_per_deg_lon"] * sx, (n - la) * grid["m_per_deg_lat"] * sy)
                for lo, la in ring]

    for f in features:
        props = {k.lower(): v for k, v in (f.get("properties") or {}).items()}
        if props.get("ftype") not in ftypes or not f.get("geometry"):
            continue
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            d.polygon(pts(poly[0]), fill=255)
            for hole in poly[1:]:
                d.polygon(pts(hole), fill=0)
    a = np.asarray(img, dtype=np.float32) / 255
    return a.reshape(ny, supersample, nx, supersample).mean(axis=(1, 3))


def smooth_noise(shape, sigma, seed):
    rng = np.random.default_rng(seed)
    f = ndimage.gaussian_filter(rng.standard_normal(shape), sigma)
    return f / (f.std() + 1e-9)


def remove_specks(cls, min_cells, keep=()):
    """Dissolve connected regions smaller than min_cells into their neighbours."""
    cls = cls.copy()
    for _ in range(3):
        bad = np.zeros(cls.shape, bool)
        for c in (ROCK, SNOW, TREE, WATER):
            if c in keep:
                continue
            lab, nlab = ndimage.label(cls == c)
            if nlab == 0:
                continue
            sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, nlab + 1))
            small = np.zeros(nlab + 1, bool)
            small[1:] = sizes < min_cells
            bad |= small[lab]
        if not bad.any():
            break
        _, (ri, rj) = ndimage.distance_transform_edt(bad, return_indices=True)
        cls = cls[ri, rj]
    return cls


def fix_diagonals(cls):
    """Remove checkerboard corners (two same-color cells touching only at a
    corner), which would make the color bodies non-manifold."""
    cls = cls.copy()
    for _ in range(50):
        A, B = cls[:-1, :-1], cls[:-1, 1:]
        C, D = cls[1:, :-1], cls[1:, 1:]
        p1 = (A == D) & (B != A) & (C != A)
        p2 = (B == C) & (A != B) & (D != B)
        if not (p1.any() or p2.any()):
            break
        i, j = np.nonzero(p1)
        cls[i, j + 1] = cls[i, j]            # B <- A joins the A-D diagonal
        p2 &= ~p1
        i, j = np.nonzero(p2)
        cls[i, j] = cls[i, j + 1]            # A <- B joins the B-C diagonal
    return cls


def majority(cls, iters=2):
    for _ in range(iters):
        counts = np.stack([ndimage.uniform_filter((cls == c).astype(float), 3, mode="nearest")
                           for c in (ROCK, SNOW, TREE)])
        best = np.argmax(counts, axis=0)
        cls = np.where(cls == WATER, WATER, best)
    return cls


def classify(elev_cell, slope, aspect, water_frac, ice_frac, args, shape):
    noise = smooth_noise(shape, sigma=6, seed=14505)
    patch = smooth_noise(shape, sigma=3, seed=4421)
    northness = np.cos(aspect)  # +1 = north-facing

    snowline = args.snowline - args.north_bonus * northness + 60 * noise
    snow = (elev_cell > snowline) & (slope < args.max_snow_slope)
    snow |= ice_frac > 0.4

    treeline = args.treeline + 70 * noise
    trees = (elev_cell < treeline) & (slope < args.max_tree_slope) & (patch > -0.7)

    cls = np.full(shape, ROCK, np.uint8)
    cls[trees] = TREE
    cls[snow] = SNOW
    cls = majority(cls)
    cls[water_frac > 0.5] = WATER
    cls = remove_specks(cls, args.min_patch)
    return fix_diagonals(cls)


# ------------------------------------------------------------------------ mesh

def body_mesh(top_lvl, bot_lvl, Z, ny, nx):
    """
    Watertight mesh for a solid made of grid-cell columns.

    top_lvl/bot_lvl: (ny, nx) int arrays giving the vertex level of the column's
    top and bottom for each cell (-1 = cell not in this body). Z: (L, ny+1, nx+1)
    z per level. Levels must be ordered so that a higher level is never lower.
    Returns (vertices, faces) with only the referenced vertices.
    """
    L = Z.shape[0]
    NV = (ny + 1) * (nx + 1)
    vid = lambda lvl, i, j: lvl * NV + i * (nx + 1) + j  # noqa: E731

    inb = top_lvl >= 0
    ci, cj = np.nonzero(inb)
    t = top_lvl[ci, cj]
    b = bot_lvl[ci, cj]
    SW = lambda l: vid(l, ci + 1, cj)       # noqa: E731
    SE = lambda l: vid(l, ci + 1, cj + 1)   # noqa: E731
    NE = lambda l: vid(l, ci, cj + 1)       # noqa: E731
    NW = lambda l: vid(l, ci, cj)           # noqa: E731

    faces = [
        np.stack([SW(t), SE(t), NE(t)], 1), np.stack([SW(t), NE(t), NW(t)], 1),   # top (up)
        np.stack([SW(b), NE(b), SE(b)], 1), np.stack([SW(b), NW(b), NE(b)], 1),   # bottom (down)
    ]

    # Side walls. Edges listed in each cell's counter-clockwise order (seen from
    # above), so the outward normal is on the right of p->q.
    sides = [  # (neighbour di, dj, p corner, q corner)
        (1, 0, SW, SE),    # south
        (0, 1, SE, NE),    # east
        (-1, 0, NE, NW),   # north
        (0, -1, NW, SW),   # west
    ]
    for di, dj, P, Q in sides:
        ni, nj = ci + di, cj + dj
        outside = (ni < 0) | (ni >= ny) | (nj < 0) | (nj >= nx)
        nt = np.full(ci.shape, -1)
        ok = ~outside
        nt[ok] = top_lvl[ni[ok], nj[ok]]
        # wall spans from `lo` up to this cell's top where the neighbour doesn't cover it
        lo = np.where(nt < 0, b, nt)
        need = (nt < 0) | (nt < t)
        need &= lo < t
        if not need.any():
            continue
        # one strip per level step, so vertical edges always pair up
        for l0 in range(L - 1):
            seg = need & (lo <= l0) & (t >= l0 + 1)
            if not seg.any():
                continue
            p_lo, q_lo = P(np.full(ci.shape, l0))[seg], Q(np.full(ci.shape, l0))[seg]
            p_hi, q_hi = P(np.full(ci.shape, l0 + 1))[seg], Q(np.full(ci.shape, l0 + 1))[seg]
            faces.append(np.stack([p_lo, q_lo, q_hi], 1))
            faces.append(np.stack([p_lo, q_hi, p_hi], 1))

    F = np.concatenate(faces)
    used, F = np.unique(F, return_inverse=True)
    F = F.reshape(-1, 3)
    lvl, rem = np.divmod(used, NV)
    i, j = np.divmod(rem, nx + 1)
    V = np.stack([j, i, lvl], 1).astype(np.float64)
    return V, F, (lvl, i, j)


def to_xyz(V_idx, Z, cell, ny):
    lvl, i, j = V_idx
    return np.stack([j * cell, (ny - i) * cell, Z[lvl, i, j]], 1)


# ---------------------------------------------------------------------- output

def write_stl(path, V, F):
    tri = V[F]
    nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    rec = np.zeros(len(F), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = nrm, tri
    with open(path, "wb") as fh:
        fh.write(b"Mt Whitney relief".ljust(80, b" "))
        fh.write(np.uint32(len(F)).tobytes())
        fh.write(rec.tobytes())


def write_3mf(path, bodies):
    """One object made of four parts (components), each with its own color."""
    mats = "".join(
        f'<base name="{NAMES[c]}" displaycolor="#{r:02X}{g:02X}{b:02X}FF"/>'
        for c, (r, g, b) in RGB.items())
    objs, comps = [], []
    oid = 2
    for c, (V, F) in bodies.items():
        vs = "".join(f'<vertex x="{x:.3f}" y="{y:.3f}" z="{z:.3f}"/>' for x, y, z in V)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{d}"/>' for a, b, d in F)
        objs.append(f'<object id="{oid}" type="model" name="{NAMES[c]}" pid="1" pindex="{c}">'
                    f'<mesh><vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>')
        comps.append(f'<component objectid="{oid}"/>')
        oid += 1
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<metadata name="Title">Mt Whitney exaggerated relief</metadata>'
        f'<resources><basematerials id="1">{mats}</basematerials>{"".join(objs)}'
        f'<object id="{oid}" type="model" name="Mt Whitney relief"><components>{"".join(comps)}'
        f'</components></object></resources>'
        f'<build><item objectid="{oid}"/></build></model>')
    ct = ('<?xml version="1.0" encoding="UTF-8"?>\n'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def write_preview(path, S, cls, cell):
    """Top-down color map with hillshade, for a quick look before slicing."""
    Sc = 0.25 * (S[:-1, :-1] + S[1:, :-1] + S[:-1, 1:] + S[1:, 1:])
    gy, gx = np.gradient(Sc, cell)
    az, alt = math.radians(315), math.radians(40)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    shade = np.clip(0.35 + 0.75 * shade, 0, 1.2)[..., None]
    col = np.zeros(cls.shape + (3,))
    for c, rgb in RGB.items():
        col[cls == c] = rgb
    img = np.clip(col * shade, 0, 255).astype(np.uint8)
    Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2), Image.LANCZOS).save(path)


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bbox", type=float, nargs=4, default=DEFAULT_BBOX,
                    metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    ap.add_argument("--width", type=float, default=200, help="model width in mm (P1S bed is 256)")
    ap.add_argument("--cell", type=float, default=0.5, help="mesh grid spacing in mm")
    ap.add_argument("--exaggeration", type=float, default=1.75, help="vertical exaggeration")
    ap.add_argument("--base", type=float, default=5.0, help="base thickness under the lowest point, mm")
    ap.add_argument("--skin", type=float, default=1.4,
                    help="thickness of the white/green/blue layers on top of the gray core, mm")
    ap.add_argument("--snowline", type=float, default=3700, help="m; snow above this on gentle slopes")
    ap.add_argument("--north-bonus", type=float, default=200, help="m lower snowline on north faces")
    ap.add_argument("--max-snow-slope", type=float, default=36, help="deg; steeper stays bare rock")
    ap.add_argument("--treeline", type=float, default=3380, help="m; trees below this")
    ap.add_argument("--max-tree-slope", type=float, default=34, help="deg")
    ap.add_argument("--min-patch", type=int, default=10, help="drop color specks smaller than N cells")
    ap.add_argument("--zoom", type=int, default=13, help="terrain tile zoom (13 ~= 15 m pixels)")
    ap.add_argument("--out", type=Path, default=HERE / "output")
    args = ap.parse_args()

    cache = HERE / ".cache"
    args.out.mkdir(parents=True, exist_ok=True)

    print("1/5 grid")
    g = build_grid(args)
    nx, ny, cell = g["nx"], g["ny"], g["cell"]
    print(f"    {g['width_m']/1000:.1f} x {g['height_m']/1000:.1f} km -> "
          f"{args.width:.0f} x {ny*cell:.0f} mm, 1:{1/g['scale']*1000:,.0f}, grid {nx}x{ny}")

    print("2/5 elevation")
    dem, px0, py0 = load_dem(args.bbox, args.zoom, cache)
    elev = sample_dem(dem, px0, py0, args.zoom, g["LON"], g["LAT"])
    elev = ndimage.gaussian_filter(elev, 0.6)
    print(f"    elevation {elev.min():.0f} - {elev.max():.0f} m")

    print("3/5 lakes, ice, snow, trees")
    nhd = load_nhd(args.bbox, cache)
    water = rasterize(nhd["features"], g, args.bbox, ftypes={390, 436, 493})
    ice = rasterize(nhd["features"], g, args.bbox, ftypes={378})

    # flatten each lake to a single water level
    lake_cells = water > 0.5
    lab, nlab = ndimage.label(lake_cells)
    for k in range(1, nlab + 1):
        ci, cj = np.nonzero(lab == k)
        vi = np.concatenate([ci, ci + 1, ci, ci + 1])
        vj = np.concatenate([cj, cj, cj + 1, cj + 1])
        elev[vi, vj] = np.percentile(elev[vi, vj], 15)

    ec = 0.25 * (elev[:-1, :-1] + elev[1:, :-1] + elev[:-1, 1:] + elev[1:, 1:])
    cell_m = cell / g["scale"]
    dzdx = 0.5 * ((elev[:-1, 1:] - elev[:-1, :-1]) + (elev[1:, 1:] - elev[1:, :-1])) / cell_m
    dzdy = 0.5 * ((elev[:-1, :-1] - elev[1:, :-1]) + (elev[:-1, 1:] - elev[1:, 1:])) / cell_m  # +y = north
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    aspect = np.arctan2(-dzdx, -dzdy)  # direction the slope faces, 0 = north
    cls = classify(ec, slope, aspect, water, ice, args, (ny, nx))
    for c in NAMES:
        print(f"    {NAMES[c]:12s} {np.mean(cls == c)*100:5.1f}% of top surface")

    print("4/5 mesh")
    base_elev = elev.min()
    S = args.base + (elev - base_elev) * g["scale"] * args.exaggeration
    S = np.maximum(S, args.base)
    Z = np.stack([np.zeros_like(S), S - args.skin, S])  # levels: 0 floor, 1 under-skin, 2 surface
    assert (S - args.skin > 0.5).all()

    bodies = {}
    colored = cls != ROCK
    top = np.where(colored, 1, 2)
    V, F, idx = body_mesh(top, np.zeros_like(top), Z, ny, nx)
    bodies[ROCK] = (to_xyz(idx, Z, cell, ny), F)
    for c in (SNOW, TREE, WATER):
        m = cls == c
        if not m.any():
            continue
        V, F, idx = body_mesh(np.where(m, 2, -1), np.where(m, 1, -1), Z, ny, nx)
        bodies[c] = (to_xyz(idx, Z, cell, ny), F)

    try:
        import trimesh
        for c, (V, F) in bodies.items():
            tm = trimesh.Trimesh(V, F, process=False)
            print(f"    {NAMES[c]:12s} {len(F):>8,} tris  watertight={tm.is_watertight} "
                  f"winding_ok={tm.is_winding_consistent} volume={tm.volume/1000:6.1f} cm3")
    except ImportError:
        pass

    print("5/5 write")
    for c, (V, F) in bodies.items():
        write_stl(args.out / f"whitney_{SLOT[c]}_{NAMES[c]}.stl", V, F)
    write_3mf(args.out / "whitney_relief.3mf", bodies)
    write_preview(args.out / "preview_topdown.png", S, cls, cell)
    np.savez_compressed(cache / "last_build.npz", S=S, cls=cls, cell=cell)
    print(f"    height {S.max():.1f} mm, summit marker at {WHITNEY}")
    print(f"    files in {args.out}")


if __name__ == "__main__":
    main()
