# -*- coding: utf-8 -*-
"""c1_stare_sweep.py —— 现有 STARE 图：今天的"直接拉伸到 512x512"对比"保持长宽比 + 分块"，以及尺度扫描。

模型 0 = lwnet 官方 wnet_drive 的第一阶段（DRIVE 训练，DRIVE 565x584 直接拉伸到 512x512）。
20 张 STARE 在 9 月选模型时已经用过（开发集），所以这里的数字只作相对比较和定 1 倍尺度 D0，不是独立测试。

对每张图：
  squash  ：原图 LANCZOS 拉伸到 512x512（= 上位机今天发的帧）→ 整网 → logit；
  aspect D：原图按比例缩放到宽 D 像素（长宽比不变，补黑到 8 的倍数；D = 600 即 1 倍）→ 整网（= 分块拼接，c1_exactness 证明逐位相同）。
logit 双线性插值回原图 700x605 后按门限 -3（模型 0 默认）判血管，与 AH 标注比：视野（亮度 > 20）内 Dice、灵敏度、
细血管召回（AH 里被 3x3 开运算去掉的像素 = 宽度 < 3 像素的血管）。

用法：python c1_stare_sweep.py [--jobs N] → results/c1_stare_sweep.json
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

STARE = r"E:\PLD\medical_fpga\data\stare\png"
MASKS = r"E:\PLD\lwnet\data\STARE\mask"
THR = -3
D_LIST = (320, 400, 440, 480, 520, 560, 600, 640, 760, 900)   # 层宽（原图宽 700 → D）


def to_native(logits, valid, size):
    """层 logit（有效区 valid = (宽, 高)）→ 双线性回原图尺寸 size = (宽, 高)，浮点。"""
    lw, lh = valid
    im = Image.fromarray(logits[:lh, :lw].astype(np.float32), mode="F")
    return np.asarray(im.resize(size, Image.BILINEAR))


def metrics(pred, gt, fov, thin):
    p, g = pred & fov, gt & fov
    tp = int((p & g).sum())
    dice = 2.0 * tp / max(1, int(p.sum()) + int(g.sum()))
    return dict(dice=dice, sens=tp / max(1, int(g.sum())),
                spec=float((~p & ~g & fov).sum()) / max(1, int((~g & fov).sum())),
                thin_recall=float((p & thin).sum()) / max(1, int(thin.sum())))


def one(name):
    from tile_ref import Ref
    ref = Ref(limit=False, max_cache=1)
    rgb = np.asarray(Image.open(os.path.join(STARE, "images", name + ".png")).convert("RGB"))
    h0, w0 = rgb.shape[:2]
    gt = np.asarray(Image.open(os.path.join(STARE, "labels_ah", name + ".png")).convert("L")) > 127
    fov = te.fov_mask(rgb)          # STARE 没有官方视野掩膜：亮度 > 20（与板端跳块同一判据）
    thin = gt & ~ndimage.binary_opening(gt, np.ones((3, 3), bool))
    fw = te.fov_width(fov)
    out = dict(name=name, size=[w0, h0], fov_w=fw, runs={})
    sq = np.asarray(Image.fromarray(rgb).resize((512, 512), Image.LANCZOS))
    t0 = time.perf_counter()
    lg = ref.run(sq)
    out["runs"]["squash512"] = dict(metrics(to_native(lg, (512, 512), (w0, h0)) >= THR, gt, fov, thin),
                                    level=[512, 512], px=512 * 512, sec=round(time.perf_counter() - t0, 1),
                                    fov_w_model=round(fw * 512.0 / w0, 1))
    for D in D_LIST:
        s = D / float(w0)
        lvl, valid = te.make_level(rgb, s)
        t0 = time.perf_counter()
        lg = ref.run(lvl)
        H, W = lvl.shape[:2]
        tiles = te.plan((0, 0, W, H), W, H)
        te.mark_fov_skip(tiles, lvl)
        c = te.costs(tiles)
        out["runs"]["aspect%d" % D] = dict(metrics(to_native(lg, valid, (w0, h0)) >= THR, gt, fov, thin),
                                          level=[W, H], px=W * H, sec=round(time.perf_counter() - t0, 1),
                                          tiles=c["tiles"], tiles_run=c["run"], computed_px=c["computed_px"])
        ref.close()
        ref = Ref(limit=False, max_cache=1)
    ref.close()
    return out


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 8
    names = sorted(os.path.splitext(f)[0] for f in os.listdir(os.path.join(STARE, "images")))
    t0 = time.perf_counter()
    with Pool(jobs) as pool:
        per = pool.map(one, names)
    keys = list(per[0]["runs"])
    summary = {}
    for k in keys:
        v = {m: float(np.mean([p["runs"][k][m] for p in per])) for m in ("dice", "sens", "spec", "thin_recall")}
        v["tiles_mean"] = float(np.mean([p["runs"][k].get("tiles", 1) for p in per]))
        v["px_mean"] = float(np.mean([p["runs"][k]["px"] for p in per]))
        v["win_vs_squash"] = int(sum(p["runs"][k]["dice"] > p["runs"]["squash512"]["dice"] for p in per))
        summary[k] = v
    best = max((k for k in keys if k.startswith("aspect")), key=lambda k: summary[k]["dice"])
    rep = dict(model="model 0 = lwnet wnet_drive stage 1 (DRIVE-trained, 512x512 squash), threshold -3",
               note="20 STARE images = development set (used for model selection 2026-09); relative comparison only",
               images=len(per), fov_w_mean=float(np.mean([p["fov_w"] for p in per])),
               squash_fov_w_model_mean=float(np.mean([p["runs"]["squash512"]["fov_w_model"] for p in per])),
               summary=summary, best_aspect=best, per_image=per, wall_s=round(time.perf_counter() - t0, 1))
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    json.dump(rep, open(os.path.join(HERE, "results", "c1_stare_sweep.json"), "w"), indent=1)
    print("STARE 20 张（开发集），视野平均宽 %.0f 像素，拉伸 512 后视野宽 %.0f"
          % (rep["fov_w_mean"], rep["squash_fov_w_model_mean"]))
    print("%-11s %7s %7s %7s %8s %6s %9s %5s" % ("方式", "Dice", "灵敏度", "特异度", "细血管召回", "块数", "像素", "胜/20"))
    for k in keys:
        v = summary[k]
        print("%-11s %7.4f %7.4f %7.4f %8.4f %6.1f %9.0f %5d" % (k, v["dice"], v["sens"], v["spec"], v["thin_recall"],
                                                            v["tiles_mean"], v["px_mean"], v["win_vs_squash"]))
    print("最好的保持长宽比尺度：%s" % best)


if __name__ == "__main__":
    main()
