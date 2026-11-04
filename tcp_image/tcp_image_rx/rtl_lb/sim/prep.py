"""Prepare the old-vs-new equivalence sim: renamed copies of rtl/ (suffix _old) + test images."""
import re, sys
from pathlib import Path
import numpy as np
from PIL import Image

here = Path(__file__).resolve().parent
old = here.parents[1] / "rtl"
mods = ["img_proc_axil", "img_proc_axis", "gray_frame_store", "mode_map", "pcolor_lut", "recip_lut"]
(here / "old").mkdir(exist_ok=True)
for f in old.glob("*.v"):
    s = f.read_text(encoding="utf-8")
    for m in mods:
        s = re.sub(r"\b%s\b" % m, m + "_old", s)
    (here / "old" / f.name.replace(".v", "_old.v")).write_text(s, encoding="utf-8")

rng = np.random.default_rng(20261013)
imgs = []
# 0: real fundus image (grey), 1: uniform random, 2: constant (range 0), 3: narrow range + gradient
src = None
for cand in [Path(r"E:/PLD/board_acceptance_pp/inputs"), Path(r"E:/PLD/board_acceptance_pp")]:
    pngs = sorted(cand.glob("*input*.png")) + sorted(cand.glob("*.png"))
    if pngs:
        src = pngs[0]; break
if src is not None:
    a = np.asarray(Image.open(src).convert("RGB").resize((512, 512)), dtype=np.uint16)
    g = ((77 * a[..., 0] + 150 * a[..., 1] + 29 * a[..., 2]) >> 8).astype(np.uint8)
    print("real image:", src)
else:
    g = rng.integers(0, 256, (512, 512), dtype=np.uint8)
imgs.append(g)
imgs.append(rng.integers(0, 256, (512, 512), dtype=np.uint8))
imgs.append(np.full((512, 512), 77, np.uint8))
yy, xx = np.mgrid[0:512, 0:512]
imgs.append((60 + ((xx + 2 * yy) % 41) + rng.integers(0, 3, (512, 512))).astype(np.uint8))
for i, g in enumerate(imgs):
    with open(here / f"img{i}.hex", "w") as fp:
        fp.write("\n".join("%02x" % v for v in g.ravel()))
    print(f"img{i}: min {g.min()} max {g.max()}")
