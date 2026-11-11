#!/usr/bin/env python3
"""Write output/viewer.html: an interactive 3D view of the assembled, painted model."""
import base64
from pathlib import Path

import numpy as np
import trimesh

from build_rf4c import COLORS

HERE = Path(__file__).resolve().parent


def load():
    d = np.load(HERE / ".cache_assembled.npz")
    names = sorted({k[:-2] for k in d.files})
    return {n: (d[f"{n}_V"], d[f"{n}_F"]) for n in names}


def main():
    scene = trimesh.Scene()
    for n, (V, F) in load().items():
        m = trimesh.Trimesh(V, F, process=True)
        r, g, b = COLORS[n]
        m.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=[r / 255, g / 255, b / 255, 1.0], metallicFactor=0.0, roughnessFactor=0.85))
        scene.add_geometry(m, node_name=n)
    glb = scene.export(file_type="glb")
    html = (HERE / "viewer_template.html").read_text().replace("__GLB__", base64.b64encode(glb).decode())
    (HERE / "output" / "viewer.html").write_text(html)
    print(f"wrote viewer.html ({len(html)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
