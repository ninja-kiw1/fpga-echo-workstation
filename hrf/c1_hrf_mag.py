# -*- coding: utf-8 -*-
"""c1_hrf_mag.py —— HRF 训练集（Orlando 2017 划分的 15 张）上 Dice 对放大倍数：定"重推理"的放大上限。

模型 0 原样（不改权重）。每张图、每个视野宽 D（层 = 原图按 D / 视野宽缩放，保持长宽比）：
  层图 → 整数逐位参考（大层用分块，c1_exactness 证明与整幅一次推理逐位相同）→ logit 双线性回 3504x2336
  → 门限 -3 → 与官方人工标注在官方视野掩膜内比：Dice、灵敏度、特异度、
  细血管召回（标注里被 5x5 开运算去掉的像素 ≈ 宽度 < 5 像素）、极细血管召回（3x3 开运算，宽 < 3 像素）。
D = 层宽（原图宽 3504 缩到 D）；放大倍数 m = D / 600（tile_engine.W1X，STARE 尺度扫描定）。测试集（30 张）这里一张都不碰。

用法：python c1_hrf_mag.py [--jobs N] [--names 01_h,...] → results/c1_hrf_mag.json
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"E:\PLD\tcp_image\tcp_image_ui")
import tile_engine as te          # noqa: E402

ALL = os.path.join(HERE, "all")
SPLIT = json.load(open(os.path.join(HERE, "split.json"), encoding="utf-8"))
THR = -3
D_LIST = (450, 600, 750, 900, 1200, 1500, 1800, 2400, 3000, 0)    # 层宽；0 = 原始分辨率 3504
WHOLE_MAX_PX = 1_600_000                                        # 大于这个就分块算（同一结果，省内存）


def load(name):
    f = SPLIT["files"][name]
    rgb = np.asarray(Image.open(os.path.join(ALL, "images", f["image"])).convert("RGB"))
    gt = np.asarray(Image.open(os.path.join(ALL, "manual1", f["manual"])).convert("L")) > 127
    fov = np.asarray(Image.open(os.path.join(ALL, "mask", f["mask"])).convert("L")) > 127
    return rgb, gt, fov


def to_native(logits, valid, size):
    lw, lh = valid
    im = Image.fromarray(logits[:lh, :lw].astype(np.float32), mode="F")
    return np.asarray(im.resize(size, Image.BILINEAR))


def metrics(pred, gt, fov, thin, vthin):
    p, g = pred & fov, gt & fov
    tp = int((p & g).sum())
    return dict(dice=2.0 * tp / max(1, int(p.sum()) + int(g.sum())), sens=tp / max(1, int(g.sum())),
                spec=float((~p & ~g & fov).sum()) / max(1, int((~g & fov).sum())),
                thin_recall=float((p & thin).sum()) / max(1, int(thin.sum())),
                vthin_recall=float((p & vthin).sum()) / max(1, int(vthin.sum())),
                pred_px=int(p.sum()))


def one(name):
    from tile_ref import Ref
    rgb, gt, fov = load(name)
    h0, w0 = rgb.shape[:2]
    gt = gt & fov
    thin = gt & ~ndimage.binary_opening(gt, np.ones((5, 5), bool))
    vthin = gt & ~ndimage.binary_opening(gt, np.ones((3, 3), bool))
    fw = te.fov_width(fov)
    out = dict(name=name, fov_w=fw, runs={})
    for D in D_LIST:
        s = 1.0 if D == 0 else D / float(w0)
        lvl, valid = te.make_level(rgb, s)
        H, W = lvl.shape[:2]
        t0 = time.perf_counter()
        if W * H <= WHOLE_MAX_PX:
            ref = Ref(limit=False, max_cache=1)
            lg = ref.run(lvl)
            tiles = te.plan((0, 0, W, H), W, H)
            te.mark_fov_skip(tiles, lvl)
        else:
            ref = Ref(limit=True, max_cache=12)
            lg, tiles = te.run_tiles(lvl, (0, 0, W, H), ref.run, skip_fov=True)
        ref.close()
        c = te.costs(tiles)
        key = "native" if D == 0 else "D%d" % D
        out["runs"][key] = dict(metrics(to_native(lg, valid, (w0, h0)) >= THR, gt, fov, thin, vthin),
                                D=w0 if D == 0 else D, scale=s, level=[W, H], tiles=c["tiles"], run=c["run"],
                                skipped=c["skipped"], computed_px=c["computed_px"], own_px=c["own_px"],
                                halo_overhead=c["halo_overhead"], sec=round(time.perf_counter() - t0, 1))
    return out


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 12
    names = (sys.argv[sys.argv.index("--names") + 1].split(",") if "--names" in sys.argv else SPLIT["train"])
    assert all(n in SPLIT["train"] for n in names), "c1_hrf_mag.py only runs on TRAIN images"
    t0 = time.perf_counter()
    with Pool(jobs) as pool:
        per = pool.map(one, names)
    keys = list(per[0]["runs"])
    d0 = te.W1X
    summary = {}
    for k in keys:
        rs = [p["runs"][k] for p in per]
        v = {m: float(np.mean([r[m] for r in rs])) for m in ("dice", "sens", "spec", "thin_recall", "vthin_recall",
                                                             "halo_overhead")}
        v.update(mag=float(np.mean([r["D"] for r in rs])) / d0, D=float(np.mean([r["D"] for r in rs])),
                 tiles=float(np.mean([r["tiles"] for r in rs])), run=float(np.mean([r["run"] for r in rs])),
                 skipped=float(np.mean([r["skipped"] for r in rs])),
                 skip_frac=float(np.mean([r["skipped"] / max(1, r["tiles"]) for r in rs])),
                 level_px=float(np.mean([r["level"][0] * r["level"][1] for r in rs])),
                 by_cat={c: float(np.mean([p["runs"][k]["dice"] for p in per if p["name"].endswith("_" + c)]))
                         for c in ("h", "g", "dr")})
        summary[k] = v
    peak = max(keys, key=lambda k: summary[k]["dice"])
    rep = dict(model="model 0 unchanged (lwnet wnet_drive stage 1, int8 board weights), threshold -3",
               split="TRAIN only (Orlando 2017: 1-5 of each category)", images=names, d0=d0,
               fov_w_mean=float(np.mean([p["fov_w"] for p in per])), summary=summary, peak=peak, per_image=per,
               wall_s=round(time.perf_counter() - t0, 1))
    json.dump(rep, open(os.path.join(HERE, "results", "c1_hrf_mag.json"), "w"), indent=1)
    print("HRF 训练集 %d 张，视野平均宽 %.0f 像素；1 倍 = 图宽 %d" % (len(names), rep["fov_w_mean"], d0))
    print("%-7s %5s %6s %6s %6s %6s %6s %6s %5s %5s %6s" % ("层", "倍数", "Dice", "灵敏度", "特异度", "细<5", "细<3",
                                                         "健康", "块", "跳过", "halo"))
    for k in keys:
        v = summary[k]
        print("%-7s %5.2f %6.4f %6.4f %6.4f %6.4f %6.4f %6.4f %5.1f %5.1f %6.3f" % (
            k, v["mag"], v["dice"], v["sens"], v["spec"], v["thin_recall"], v["vthin_recall"], v["by_cat"]["h"],
            v["tiles"], v["skipped"], v["halo_overhead"]))
    print("Dice 最高：%s" % peak)


if __name__ == "__main__":
    main()
