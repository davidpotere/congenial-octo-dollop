#!/usr/bin/env python3
"""Oblique 3D preview of the last build (looking west at Whitney's east face, as from Lone Pine)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LightSource  # noqa: E402

HERE = Path(__file__).resolve().parent
RGB = np.array([(128, 128, 128), (245, 245, 245), (40, 120, 50), (40, 90, 200)]) / 255


def _trim(path):
    from PIL import Image, ImageChops
    im = Image.open(path).convert("RGB")
    box = ImageChops.difference(im, Image.new("RGB", im.size, "white")).getbbox()
    if box:
        im.crop((max(box[0] - 20, 0), max(box[1] - 20, 0), box[2] + 20, box[3] + 20)).save(path)


def main(step=2):
    d = np.load(HERE / ".cache" / "last_build.npz")
    S, cls, cell = d["S"], d["cls"], float(d["cell"])
    Sc = 0.25 * (S[:-1, :-1] + S[1:, :-1] + S[:-1, 1:] + S[1:, 1:])
    Sc, cls = Sc[::step, ::step], cls[::step, ::step]
    ny, nx = Sc.shape
    X, Y = np.meshgrid(np.arange(nx) * cell * step, (ny - np.arange(ny)) * cell * step)
    ls = LightSource(azdeg=110, altdeg=35)
    rgb = ls.shade_rgb(RGB[cls], Sc, vert_exag=1.0, blend_mode="soft", fraction=1.0)

    for name, azim, elev in (("preview_from_east.png", 5, 28), ("preview_from_southwest.png", 235, 32)):
        fig = plt.figure(figsize=(12, 8), dpi=150)
        ax = fig.add_subplot(projection="3d")
        ax.plot_surface(X, Y, Sc, facecolors=rgb, rstride=1, cstride=1, linewidth=0,
                        antialiased=False, shade=False)
        ax.set_box_aspect((X.max(), Y.max(), Sc.max()))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        fig.patch.set_facecolor("white")
        plt.subplots_adjust(0, 0, 1, 1)
        fig.savefig(HERE / "output" / name, bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        _trim(HERE / "output" / name)
        print("wrote", name)


if __name__ == "__main__":
    main()
