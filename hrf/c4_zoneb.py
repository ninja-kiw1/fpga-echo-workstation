# -*- coding: utf-8 -*-
"""c4_zoneb.py —— 部分 C4（可选）：视盘居中的 Zone B 自动 ROI + 管径（Knudtson CRAE / CRVE）+ A/V 可行性。

只用 HRF **训练集** 15 张（Orlando 2017：每类 1–5 号）。测试集 30 张已在部分 C 用过一次（results/test_lock.json），
这里一张都不碰（每个入口都 assert）。

子命令（结果都在 results/c4/）：
  disc     程序 19（fundus_disc，整数 = 板上）按部分 D 的流程在 512 拉伸总览上定位视盘 → 原图坐标的中心 + 直径；
           与 HRF optic_disk_centers.xls 两位专家的中心 / 直径比（专家间差异一起报）。
  tiles    Zone B 框（视盘中心 ± 1.5 DD + 余量，专家环和程序 19 环的并集）→ 层（tile_engine.make_level）→
           按 JOB_TILE 的同一规划分块跑模型 0 的整数逐位参考（tile_ref.Ref，= 板上 JOB_TILE，部分 C1 / C2 证明）
           → 框内 logit（int8）。倍数 1 / 2 / 2.5（2 = HRF 训练集 Dice 峰值）。
  av       lwnet big_wnet_hrf_av_1024（MIT，FP32，PC）出 A/V 概率。该模型在 HRF 6–15 号上训练，
           1–5 号（= 这里的训练集）是它的 A/V 测试集——对它来说是没见过的图。
  measure  Zone B 环上量管径，与人工分割（manual1）上同一算法量的管径比；Knudtson 汇总；A/V 一致性。

定义见 C4 报告（c4_zone_b_vessel_calibre_2026-10-18.md）。测量辅助，不作诊断。
"""
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"E:\PLD\tcp_image\tcp_image_ui")
import tile_engine as te          # noqa: E402

ALL = os.path.join(HERE, "all")
OUT = os.path.join(HERE, "results", "c4")
SPLIT = json.load(open(os.path.join(HERE, "split.json"), encoding="utf-8"))
TRAIN = SPLIT["train"]
TEST = set(SPLIT["test"])
XLS_CSV = os.path.join(HERE, "raw", "optic_disk_centers_csv")     # 两个工作表导出的 CSV（Excel 原样另存）
AV_GT = r"E:\PLD\lwnet\data\HRF\manual_av"
MAGS = (1, 2, 2.5)
THR = -3                     # 模型 0 的门限（与部分 C 相同）
ZONE_IN, ZONE_OUT = 1.0, 1.5  # Zone B：离视盘边缘 0.5–1 DD = 离中心 1.0–1.5 DD（视盘半径 0.5 DD）
PAD_DD = 0.2                 # 框比环外缘再多 0.2 DD（法向剖面要用到环外一点像素）


def guard(names):
    bad = [n for n in names if n in TEST or n not in TRAIN]
    assert not bad, "C4 runs on HRF TRAIN only; refused: %s" % bad


def load_rgb(name):
    f = SPLIT["files"][name]
    return np.asarray(Image.open(os.path.join(ALL, "images", f["image"])).convert("RGB"))


def load_gt(name):
    f = SPLIT["files"][name]
    gt = np.asarray(Image.open(os.path.join(ALL, "manual1", f["manual"])).convert("L")) > 127
    fov = np.asarray(Image.open(os.path.join(ALL, "mask", f["mask"])).convert("L")) > 127
    return gt, fov


def experts():
    """两位专家：{name: {"A": (cx, cy, dd, ox, oy), "B": ...}}（xls 的列：中心 x / y、血管起点 x / y、视盘直径）。"""
    out = {}
    for tag, fn in (("A", "Expert A.csv"), ("B", "Expert B.csv")):
        for line in open(os.path.join(XLS_CSV, fn), encoding="utf-8-sig").read().splitlines()[1:]:
            f = line.split(",")
            n = f[0] if "_" in f[0] else f[0][:2] + "_" + f[0][2:]
            cx, cy, ox, oy, dd = (float(v) for v in f[1:6])
            out.setdefault(n, {})[tag] = (cx, cy, dd, ox, oy)
    return out


def expert_ref(e):
    """参考视盘 = 两位专家的平均；某位专家的中心离他自己标的血管起点 > 0.5 DD、而另一位 < 0.5 DD 时视为录入错误，只用另一位。"""
    ok = {}
    for k, (cx, cy, dd, ox, oy) in e.items():
        ok[k] = np.hypot(cx - ox, cy - oy) <= 0.5 * dd
    use = [k for k in e if ok[k]] or list(e)
    cx = float(np.mean([e[k][0] for k in use]))
    cy = float(np.mean([e[k][1] for k in use]))
    dd = float(np.mean([e[k][2] for k in use]))
    return dict(cx=cx, cy=cy, dd=dd, used=use, dropped=[k for k in e if k not in use])


# ---------------------------------------------------------------------------------------------
# disc：程序 19 定位视盘（部分 D 的整条流程，整数模型 = 板上）
# ---------------------------------------------------------------------------------------------
def fallback_disc(img, q1, disc_p, cdr_ref):
    """部分 D 的第一阶段在 HRF 上没过门限时（训练集 3 / 15 张，程序 19 是 Chákṣu 训练的）：
    第一阶段 logit 5x5 平均后的最大处为中心，取 256 窗口跑第二阶段（同一程序 19）。
    这条规则是看了这 3 张训练图后定的 → 训练集上的数字偏乐观。只用 PS 运算，不需要新程序。"""
    from scipy import ndimage
    sm = ndimage.uniform_filter(np.asarray(q1, np.float64), 5)
    yy, xx = np.unravel_index(int(np.argmax(sm)), sm.shape)
    side = 256
    y0, x0 = cdr_ref.window(4 * yy + 2, 4 * xx + 2, side)
    qd = disc_p(cdr_ref.payload2(img, y0, x0, side))
    res = cdr_ref.measure(qd, qd, side)
    res.update(ok=res["disc_area"] > 0, y0=y0, x0=x0, side=side)
    return res


def cmd_disc(names):
    sys.path.insert(0, r"E:\PLD\fundus_cdr")
    import cdr_ref
    from common import host_stretch
    from eval_val import IntProg
    disc_p, cup_p = IntProg("disc_mixed_a"), IntProg("cup_roi_a")
    ex = experts()
    rows = []
    for n in names:
        rgb = load_rgb(n)
        h0, w0 = rgb.shape[:2]
        img = host_stretch(rgb)
        t0 = time.perf_counter()
        r = cdr_ref.run_case(img, lambda name, pay: (disc_p if name == "disc" else cup_p)(pay))
        method = "partD"
        if not r["ok"]:
            r = fallback_disc(img, r["q1"], disc_p, cdr_ref)
            method = "fallback_argmax256"
        sec = time.perf_counter() - t0
        row = dict(name=n, ok=bool(r["ok"]), method=method, sec=round(sec, 2))
        e = ex[n]
        ref = expert_ref(e)
        row["ref"] = ref
        row["experts"] = {k: dict(cx=v[0], cy=v[1], dd=v[2], ox=v[3], oy=v[4]) for k, v in e.items()}
        if "A" in e and "B" in e:
            a, b = e["A"], e["B"]
            row["inter_expert"] = dict(centre_px=float(np.hypot(a[0] - b[0], a[1] - b[1])),
                                       dd_diff_px=float(a[2] - b[2]))
        if r["ok"]:
            canvas = np.zeros((512, 512), bool)
            y0, x0, s = r["y0"], r["x0"], r["side"]
            canvas[y0:y0 + s, x0:x0 + s] = r["disc"]
            yy, xx = np.nonzero(canvas)
            sx, sy = w0 / 512.0, h0 / 512.0
            cx = (xx.mean() + 0.5) * sx - 0.5
            cy = (yy.mean() + 0.5) * sy - 0.5
            dh = (yy.max() - yy.min() + 1) * sy
            dw = (xx.max() - xx.min() + 1) * sx
            dd = 0.5 * (dh + dw)
            row.update(cx=float(cx), cy=float(cy), dd=float(dd), dd_v=float(dh), dd_h=float(dw), side=int(s),
                       centre_err_px=float(np.hypot(cx - ref["cx"], cy - ref["cy"])),
                       dd_err_px=float(dd - ref["dd"]))
            row["centre_err_dd"] = row["centre_err_px"] / ref["dd"]
            row["dd_err_rel"] = row["dd_err_px"] / ref["dd"]
            for k, v in e.items():
                row["err_vs_" + k] = dict(centre_px=float(np.hypot(cx - v[0], cy - v[1])), dd_px=float(dd - v[2]))
        rows.append(row)
        print("%-6s ok=%s centre err %.1f px (%.3f DD)  DD %.0f vs %.0f  (%s)" % (
            n, row["ok"], row.get("centre_err_px", -1), row.get("centre_err_dd", -1), row.get("dd", -1),
            ref["dd"], ",".join(ref["used"])), flush=True)
    ok = [r for r in rows if r["ok"]]
    ie = [r["inter_expert"] for r in rows if "inter_expert" in r and not r["ref"]["dropped"]]
    pd_ok = [r for r in ok if r["method"] == "partD"]
    summ = dict(n=len(rows), found=len(ok), found_partD=len(pd_ok),
                fallback=[r["name"] for r in ok if r["method"] != "partD"],
                partD_centre_err_dd_mean=float(np.mean([r["centre_err_dd"] for r in pd_ok])),
                partD_dd_err_rel_mean=float(np.mean([r["dd_err_rel"] for r in pd_ok])),
                dd_err_rel_mean=float(np.mean([r["dd_err_rel"] for r in ok])),
                centre_err_px_mean=float(np.mean([r["centre_err_px"] for r in ok])),
                centre_err_px_max=float(np.max([r["centre_err_px"] for r in ok])),
                centre_err_dd_mean=float(np.mean([r["centre_err_dd"] for r in ok])),
                centre_err_dd_max=float(np.max([r["centre_err_dd"] for r in ok])),
                dd_err_px_mean=float(np.mean([r["dd_err_px"] for r in ok])),
                dd_abs_err_px_mean=float(np.mean([abs(r["dd_err_px"]) for r in ok])),
                dd_abs_err_rel_mean=float(np.mean([abs(r["dd_err_rel"]) for r in ok])),
                inter_expert_centre_px_mean=float(np.mean([v["centre_px"] for v in ie])),
                inter_expert_centre_px_max=float(np.max([v["centre_px"] for v in ie])),
                inter_expert_dd_abs_px_mean=float(np.mean([abs(v["dd_diff_px"]) for v in ie])),
                inter_expert_n=len(ie),
                dropped_entries=[(r["name"], r["ref"]["dropped"]) for r in rows if r["ref"]["dropped"]],
                sec_mean=float(np.mean([r["sec"] for r in rows])))
    json.dump(dict(split="HRF TRAIN only", programs="19 fundus_disc (disc_mixed_a) + 20 (cup, unused here), integer",
                   summary=summ, per_image=rows), open(os.path.join(OUT, "disc.json"), "w"), indent=1)
    print(json.dumps(summ, indent=1))


def discs():
    return {r["name"]: r for r in json.load(open(os.path.join(OUT, "disc.json")))["per_image"]}


def zone_box(cx, cy, dd, w0, h0):
    """原图坐标的 Zone B 框（中心 ± (1.5 + PAD) DD，夹在图内）-> (x, y, w, h)。"""
    r = (ZONE_OUT + PAD_DD) * dd
    x0, y0 = max(0, int(np.floor(cx - r))), max(0, int(np.floor(cy - r)))
    x1, y1 = min(w0, int(np.ceil(cx + r))), min(h0, int(np.ceil(cy + r)))
    return x0, y0, x1 - x0, y1 - y0


def union(b1, b2):
    x0, y0 = min(b1[0], b2[0]), min(b1[1], b2[1])
    x1, y1 = max(b1[0] + b1[2], b2[0] + b2[2]), max(b1[1] + b1[3], b2[1] + b2[3])
    return x0, y0, x1 - x0, y1 - y0


def tiles_one(args):
    from tile_ref import Ref
    name, mag, d = args
    rgb = load_rgb(name)
    h0, w0 = rgb.shape[:2]
    box0 = union(zone_box(d["ref"]["cx"], d["ref"]["cy"], d["ref"]["dd"], w0, h0),
                 zone_box(d["cx"], d["cy"], d["dd"], w0, h0))
    s = te.scale_for_mag(mag, w0)
    lvl, valid = te.make_level(rgb, s)
    H, W = lvl.shape[:2]
    box = te.box_to_level(box0, s, W, H)
    ref = Ref(limit=True, max_cache=4)
    t0 = time.perf_counter()
    lg, tiles = te.run_tiles(lvl, box, ref.run, skip_fov=True)
    sec = time.perf_counter() - t0
    ref.close()
    c = te.costs(tiles)
    np.savez_compressed(os.path.join(OUT, "logits_%s_m%s.npz" % (name, mag)), logits=lg, box=np.array(box),
                        valid=np.array(valid), level=np.array([W, H]), s=s, box0=np.array(box0))
    return dict(name=name, mag=mag, box0=box0, box=box, level=[W, H], tiles=c["tiles"], run=c["run"],
                computed_px=c["computed_px"], own_px=c["own_px"], halo_overhead=c["halo_overhead"],
                pc_sec=round(sec, 1),
                board_ms_est=round(sum(1.10e-3 * t.tw * t.th + 3.3 for t in tiles if not t.skip), 1))


def cmd_tiles(names):
    d = discs()
    jobs = [(n, m, d[n]) for n in names for m in MAGS]
    with Pool(int(os.environ.get("C4_JOBS", "12"))) as pool:
        rows = pool.map(tiles_one, jobs)
    json.dump(dict(engine="tile_engine.plan + tile_ref.Ref (= board JOB_TILE bit for bit, part C1/C2)",
                   board_ms_est="per tile 1.10 us/px * area + 3.3 ms (part C fit on f150)", rows=rows),
              open(os.path.join(OUT, "tiles.json"), "w"), indent=1)
    for r in rows:
        print("%-6s m%-4s box %s tiles %d run %d halo %.2f  pc %.1fs  board est %.0f ms" % (
            r["name"], r["mag"], r["box"], r["tiles"], r["run"], r["halo_overhead"], r["pc_sec"], r["board_ms_est"]))


AV_EXP = r"E:\PLD\lwnet\experiments\big_wnet_hrf_av_1024"


def cmd_av(names):
    """lwnet 的 A/V 推理（generate_av_results.py 同一流程：按官方视野掩膜外接框裁剪 → 1024x1024 双线性 → /255 →
    big_wnet 4 类 → 4 种翻转平均概率 → 双三次回裁剪尺寸 → "不确定"类的概率一半给动脉、一半给静脉）。
    只存 Zone B 框（1 倍框 = 所有倍数的原图框）里的 动脉 / 静脉 概率（uint8，x255）。"""
    import torch
    import torch.nn.functional as F
    sys.path.insert(0, r"E:\PLD\lwnet")
    from models.get_model import get_arch
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = get_arch("big_wnet", n_classes=4)
    ck = torch.load(os.path.join(AV_EXP, "model_checkpoint.pth"), map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model_state_dict"])
    model.mode = "eval"
    model.eval().to(dev)
    n_par = sum(p.numel() for p in model.parameters())
    rows = []
    for n in names:
        rgb = load_rgb(n)
        _, fov = load_gt(n)
        ys, xs = np.nonzero(fov)
        r0, r1, c0, c1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        crop = Image.fromarray(rgb[r0:r1, c0:c1]).resize((1024, 1024), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(crop, np.float32) / 255.0).permute(2, 0, 1)[None].to(dev)
        t0 = time.perf_counter()
        with torch.no_grad():
            ps = []
            for fl in ([], [-1], [-2], [-1, -2]):
                xi = x.flip(fl) if fl else x
                lo = model(xi)
                lo = lo.flip(fl) if fl else lo
                ps.append(torch.softmax(lo, 1))
            p = torch.stack(ps).mean(0)                                      # 1 x 4 x 1024 x 1024
            p = F.interpolate(p, size=(int(r1 - r0), int(c1 - c0)), mode="bicubic", align_corners=False)[0]
        if dev.type == "cuda":
            torch.cuda.synchronize()
        sec = time.perf_counter() - t0
        p = p.clamp(0, 1).cpu().numpy()
        art = p[2] + 0.5 * p[1]
        vein = p[3] + 0.5 * p[1]
        z = np.load(os.path.join(OUT, "logits_%s_m1.npz" % n))
        bx, by, bw, bh = (int(v) for v in z["box0"])
        A = np.zeros(fov.shape, np.float32)
        V = np.zeros(fov.shape, np.float32)
        A[r0:r1, c0:c1], V[r0:r1, c0:c1] = art, vein
        np.savez_compressed(os.path.join(OUT, "av_%s.npz" % n), box0=np.array([bx, by, bw, bh]),
                            art=np.round(255 * A[by:by + bh, bx:bx + bw]).astype(np.uint8),
                            vein=np.round(255 * V[by:by + bh, bx:bx + bw]).astype(np.uint8))
        rows.append(dict(name=n, sec=round(sec, 2)))
        print(n, "%.2fs" % sec, flush=True)
    json.dump(dict(model="lwnet big_wnet_hrf_av_1024 (MIT), FP32, TTA 4 flips from_probs, device %s" % dev,
                   params=int(n_par), trained_on="HRF 6-15 of each category (lwnet get_public_data.py: av_train = "
                   "vessel-test images); 1-5 = its A/V test set = our TRAIN", rows=rows),
              open(os.path.join(OUT, "av.json"), "w"), indent=1)
    print("params", n_par)


# ---------------------------------------------------------------------------------------------
# measure：Zone B 上的管径
# ---------------------------------------------------------------------------------------------
N_CIRC = 6                 # Zone B 里 6 个同心圆：中心 1.0, 1.1, ..., 1.5 DD
ARC_STEP = 0.5             # 圆上采样间距（原图像素）
MERGE_GAP = 2.0            # 同一圆上相距 <= 2 像素的两段并成一段（中心反光把一根血管切成两段）
MIN_CHORD = 2.0            # 圆上 < 2 像素的段不算
PROF_STEP = 0.1            # 法向剖面采样间距
KNUD = dict(A=0.88, V=0.95)    # Knudtson 2003 分支系数：动脉 0.88、静脉 0.95；各取最大的 6 根
THR_W = (-3, -1, 0, 1, 2, 3, 4, 6, 8, 10, 12, 16, 20)   # 量管径用的 logit 等值线（找血管仍用分割门限 -3）；用哪条由留一图交叉验证定
CHORD_K, CHORD_ADD = 1.2, 2.0   # 法向宽度不能超过圆上弦长（弦 = 宽 / cos(走向与径向夹角) >= 宽）：w <= 1.2 弦 + 2，否则是分叉口


class Field(object):
    """原图坐标上的连续场 + 门限：ref = 人工分割（0/1 双线性，门限 0.5 = 像素边界）；pred = 层上的 logit 双线性回原图坐标
    （与部分 C 的 PIL BILINEAR 回原图同一像素中心约定），门限 -3。"""

    def __init__(self, kind, name, mag=None, gt=None):
        from scipy import ndimage
        self.nd = ndimage
        self.kind = kind
        if kind == "ref":
            self.img = gt.astype(np.float32)
            self.thr = 0.5
        else:
            z = np.load(os.path.join(OUT, "logits_%s_m%s.npz" % (name, mag)))
            self.lg = z["logits"].astype(np.float32)
            self.bx, self.by, self.bw, self.bh = (int(v) for v in z["box"])
            lw, lh = (int(v) for v in z["valid"])
            self.fx, self.fy = lw / float(gt.shape[1]), lh / float(gt.shape[0])
            self.thr = THR

    def __call__(self, x, y):
        x, y = np.asarray(x, np.float64), np.asarray(y, np.float64)
        if self.kind == "ref":
            return self.nd.map_coordinates(self.img, [y, x], order=1, mode="constant", cval=0.0)
        u = (x + 0.5) * self.fx - 0.5 - self.bx
        v = (y + 0.5) * self.fy - 0.5 - self.by
        out = self.nd.map_coordinates(self.lg, [v, u], order=1, mode="nearest")
        bad = (u < -0.5) | (v < -0.5) | (u > self.bw - 0.5) | (v > self.bh - 0.5)
        out[bad] = -128.0
        return out

    def mask(self, box0):
        bx, by, bw, bh = box0
        yy, xx = np.mgrid[by:by + bh, bx:bx + bw]
        return (self(xx.ravel(), yy.ravel()) >= self.thr).reshape(bh, bw)


def circ_runs(b):
    """圆上布尔序列 -> [(起点, 长度)]（首尾相接；间隔 <= MERGE_GAP 的并起来）。全 True / 全 False -> []。"""
    n = len(b)
    if b.all() or not b.any():
        return []
    k = int(np.argmin(b))                 # 从一个 False 开始，首尾就不会切开一段
    bb = np.roll(b, -k)
    d = np.diff(np.concatenate([[0], bb.astype(np.int8), [0]]))
    st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    runs = [[int(s), int(e)] for s, e in zip(st, en)]
    gap = int(round(MERGE_GAP / ARC_STEP))
    merged = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    if len(merged) > 1 and merged[0][0] + n - merged[-1][1] <= gap:
        merged[0][0] = merged[-1][0] - n
        merged.pop()
    return [((s + k) % n, e - s) for s, e in merged]


def local_dir(mask, box0, x, y, chord):
    """(x, y) 附近血管的走向：窗口里含中心最近血管像素的连通块做 PCA；拉长比 < 2 时返回 None（调用方用径向）。"""
    from scipy import ndimage
    bx, by, bw, bh = box0
    R = int(min(40, max(10, 1.5 * chord)))
    xi, yi = int(round(x)) - bx, int(round(y)) - by
    x0, y0, x1, y1 = max(0, xi - R), max(0, yi - R), min(bw, xi + R + 1), min(bh, yi + R + 1)
    w = mask[y0:y1, x0:x1]
    if not w.any():
        return None
    lab, _ = ndimage.label(w, np.ones((3, 3)))
    ys, xs = np.nonzero(lab)
    j = int(np.argmin((xs + x0 - xi) ** 2 + (ys + y0 - yi) ** 2))
    sel = lab == lab[ys[j], xs[j]]
    ys, xs = np.nonzero(sel)
    if len(xs) < 6:
        return None
    c = np.cov(np.vstack([xs, ys]).astype(np.float64))
    ev, evec = np.linalg.eigh(c)
    if ev[0] <= 1e-9 or ev[1] / ev[0] < 2.0:
        return None
    return evec[:, 1]                       # 主轴（走向）


def width_along(field, cx, cy, nx, ny, smax, L, thr=None, prof=None):
    """沿法向 (nx, ny) 的剖面：从中心 ±smax 内最近的 >= 门限点向两边走到 < 门限，线性插值求两个边界 -> (宽, 新中心)。
    prof = 已采好的剖面（同一 t 网格），换门限时不必重采。"""
    t = np.arange(-L, L + PROF_STEP / 2, PROF_STEP)
    f = (field(cx + t * nx, cy + t * ny) if prof is None else prof) - (field.thr if thr is None else thr)
    i0 = len(t) // 2
    on = np.nonzero((f >= 0) & (np.abs(t) <= smax))[0]
    if len(on) == 0:
        return None, None
    i = int(on[np.argmin(np.abs(on - i0))])
    r = i
    while r + 1 < len(t) and f[r + 1] >= 0:
        r += 1
    lft = i
    while lft - 1 >= 0 and f[lft - 1] >= 0:
        lft -= 1
    if r + 1 >= len(t) or lft - 1 < 0:
        return None, None                   # 剖面没走出血管（太宽 / 贴着别的血管）
    tr = t[r] + PROF_STEP * f[r] / (f[r] - f[r + 1])
    tl = t[lft] - PROF_STEP * f[lft] / (f[lft] - f[lft - 1])
    tm = 0.5 * (tl + tr)
    return float(tr - tl), (cx + tm * nx, cy + tm * ny)


def profile(field, cx, cy, nx, ny, L):
    t = np.arange(-L, L + PROF_STEP / 2, PROF_STEP)
    return field(cx + t * nx, cy + t * ny)


def widths_multi(field, cx, cy, nx, ny, smax, L, thrs, wmax):
    """同一剖面在几条等值线上的宽（> wmax 记 None）-> {"w@t": 宽}。"""
    pr = profile(field, cx, cy, nx, ny, L)
    out = {}
    for t in thrs:
        w, _ = width_along(field, cx, cy, nx, ny, smax, L, thr=t, prof=pr)
        out["w@%g" % t] = w if (w is not None and w <= wmax) else None
    return out


def crossings(field, mask, box0, fov, cx, cy, dd, av_gt=None, av_pred=None, thrs=()):
    """Zone B 6 个圆上的血管截点（这个场自己的掩膜）-> list of dict。"""
    bx, by, bw, bh = box0
    out = []
    for k in range(N_CIRC):
        r = dd * (ZONE_IN + (ZONE_OUT - ZONE_IN) * k / (N_CIRC - 1))
        n = int(np.ceil(2 * np.pi * r / ARC_STEP))
        th = 2 * np.pi * np.arange(n) / n
        xs, ys = cx + r * np.cos(th), cy + r * np.sin(th)
        xi, yi = np.round(xs).astype(int), np.round(ys).astype(int)
        inside = (xi >= bx) & (yi >= by) & (xi < bx + bw) & (yi < by + bh)
        inf = np.zeros(n, bool)
        inf[inside] = fov[yi[inside], xi[inside]]
        b = np.zeros(n, bool)
        b[inside] = mask[yi[inside] - by, xi[inside] - bx]
        b &= inf
        for s, ln in circ_runs(b):
            chord = ln * ARC_STEP
            if chord < MIN_CHORD:
                continue
            pad = int(round(2 / ARC_STEP))
            idx = np.arange(s - pad, s + ln + pad) % n
            if not inf[idx].all():
                continue                    # 贴着视野边
            mid = (s + ln / 2.0) % n
            tc = 2 * np.pi * mid / n
            px, py = cx + r * np.cos(tc), cy + r * np.sin(tc)
            d = local_dir(mask, box0, px, py, chord)
            radial = np.array([np.cos(tc), np.sin(tc)])
            if d is None:
                d = radial
            nx, ny = -d[1], d[0]
            L = min(60.0, 2 * chord + 10)
            w, c = width_along(field, px, py, nx, ny, 3.0, L)
            if w is None or w > CHORD_K * chord + CHORD_ADD:
                continue
            row = dict(k=k, r=r, theta=tc, half=(chord / 2.0) / r, chord=chord, x=c[0], y=c[1], nx=nx, ny=ny,
                       w=w, cos_radial=float(abs(np.dot(d, radial))))
            if thrs:
                row.update(widths_multi(field, c[0], c[1], nx, ny, 3.0, L, thrs, CHORD_K * chord + CHORD_ADD))
            run = np.arange(s, s + ln) % n
            if av_gt is not None:
                g = av_gt[yi[run], xi[run]]
                row["gtA"] = int(((g[:, 0] > 127) & (g[:, 2] <= 127)).sum())
                row["gtV"] = int(((g[:, 2] > 127) & (g[:, 0] <= 127)).sum())
            if av_pred is not None:
                ab, art, vein = av_pred
                ii, jj = yi[run] - ab[1], xi[run] - ab[0]
                ok = (ii >= 0) & (jj >= 0) & (ii < art.shape[0]) & (jj < art.shape[1])
                row["pA"] = float(art[ii[ok], jj[ok]].sum()) / 255.0
                row["pV"] = float(vein[ii[ok], jj[ok]].sum()) / 255.0
            out.append(row)
    return out


def ang_overlap(a, b, tol):
    d = abs((a["theta"] - b["theta"] + np.pi) % (2 * np.pi) - np.pi)
    return d <= a["half"] + b["half"] + tol


def group(cr):
    """相邻圆上角度区间重叠的截点连成一根血管（并查集）；跨 >= 3 个圆的才算"穿过 Zone B 的血管"。"""
    par = list(range(len(cr)))

    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    byk = {}
    for i, c in enumerate(cr):
        byk.setdefault(c["k"], []).append(i)
    for k in range(N_CIRC - 1):
        for i in byk.get(k, []):
            for j in byk.get(k + 1, []):
                if ang_overlap(cr[i], cr[j], 3.0 / cr[i]["r"]):
                    par[f(i)] = f(j)
    gs = {}
    for i in range(len(cr)):
        gs.setdefault(f(i), []).append(i)
    out = []
    for mem in gs.values():
        ks = sorted(set(cr[i]["k"] for i in mem))
        if len(ks) < 3:
            continue
        out.append(mem)
    return out


def group_width(cr, mem, key="w"):
    """一根血管的管径：只有一个截点的圆（分叉前的主干）上的中位数；每个圆都 >= 2 个时取每圆最大值的中位数。"""
    per = {}
    for i in mem:
        if cr[i].get(key) is not None:
            per.setdefault(cr[i]["k"], []).append(cr[i][key])
    if not per:
        return None, False
    single = [v[0] for v in per.values() if len(v) == 1]
    branched = any(len(v) > 1 for v in per.values())
    return (float(np.median(single)) if single else float(np.median([max(v) for v in per.values()]))), branched


def group_label(cr, mem, src):
    if src == "gt":
        a, v = sum(cr[i].get("gtA", 0) for i in mem), sum(cr[i].get("gtV", 0) for i in mem)
    else:
        a, v = sum(cr[i].get("pA", 0) for i in mem), sum(cr[i].get("pV", 0) for i in mem)
    if a == 0 and v == 0:
        return "U"
    return "A" if a >= v else "V"


def knudtson(ws, coef):
    """Knudtson 2003：取最大的 6 根；每轮最大配最小、次大配次小……（奇数时中间那根直接进下一轮），
    W = coef * sqrt(w1^2 + w2^2)，直到剩一个。"""
    w = sorted([float(v) for v in ws if v is not None], reverse=True)[:6]
    if not w:
        return None
    while len(w) > 1:
        w.sort(reverse=True)
        nxt = []
        for i in range(len(w) // 2):
            nxt.append(coef * np.hypot(w[i], w[-1 - i]))
        if len(w) % 2:
            nxt.append(w[len(w) // 2])
        w = nxt
    return float(w[0])


def summary_idx(vessels):
    """vessels: [(width, label)] -> CRAE / CRVE / AVR + 计数。"""
    A = [w for w, l in vessels if l == "A" and w is not None]
    V = [w for w, l in vessels if l == "V" and w is not None]
    crae, crve = knudtson(A, KNUD["A"]), knudtson(V, KNUD["V"])
    return dict(crae=crae, crve=crve, avr=(crae / crve) if crae and crve else None, nA=len(A), nV=len(V))


def agree(p, r):
    """成对数值的一致性：偏差、MAE、MAPE、r、ICC(A,1)（McGraw & Wong 双向随机、绝对一致、单次）、95% 一致性界限。"""
    p, r = np.asarray(p, float), np.asarray(r, float)
    ok = np.isfinite(p) & np.isfinite(r)
    p, r = p[ok], r[ok]
    n = len(p)
    if n < 3:
        return dict(n=n)
    d = p - r
    Y = np.vstack([r, p]).T
    k = 2
    gm = Y.mean()
    msr = k * ((Y.mean(1) - gm) ** 2).sum() / (n - 1)
    msc = n * ((Y.mean(0) - gm) ** 2).sum() / (k - 1)
    sse = ((Y - Y.mean(1, keepdims=True) - Y.mean(0, keepdims=True) + gm) ** 2).sum()
    mse = sse / ((n - 1) * (k - 1))
    icc = (msr - mse) / (msr + (k - 1) * mse + k * (msc - mse) / n)
    return dict(n=n, ref_mean=float(r.mean()), bias=float(d.mean()), mae=float(np.abs(d).mean()),
                mape=float(np.mean(np.abs(d) / r)), r=float(np.corrcoef(p, r)[0, 1]), icc=float(icc),
                loa=[float(d.mean() - 1.96 * d.std(ddof=1)), float(d.mean() + 1.96 * d.std(ddof=1))])


def measure_one(name):
    """一张图的原始测量：参考血管（人工分割 + 专家视盘）、各倍数下同一截点处模型的多等值线管径、
    模型自己在程序 19 环上找到的血管（多等值线管径 + 人工 / lwnet 的 A/V）、检测匹配。汇总在 cmd_measure。"""
    d = discs()[name]
    gt, fov = load_gt(name)
    av_gt = np.asarray(Image.open(os.path.join(AV_GT, name + "_AVmanual.png")).convert("RGB"))
    z = np.load(os.path.join(OUT, "av_%s.npz" % name))
    av_pred = (z["box0"], z["art"], z["vein"])
    box0 = tuple(int(v) for v in z["box0"])
    bx, by, bw, bh = box0
    rf = Field("ref", name, gt=gt)
    rmask = gt[by:by + bh, bx:bx + bw]
    ring_ref = (d["ref"]["cx"], d["ref"]["cy"], d["ref"]["dd"])
    ring_auto = (d["cx"], d["cy"], d["dd"])
    rc = crossings(rf, rmask, box0, fov, *ring_ref, av_gt=av_gt, av_pred=av_pred)
    rg = group(rc)
    ref_v = []
    for mem in rg:
        w, br = group_width(rc, mem)
        ref_v.append(dict(w=w, branched=br, gt=group_label(rc, mem, "gt"), lw=group_label(rc, mem, "pred"),
                          ks=len(set(rc[i]["k"] for i in mem))))
    res = dict(name=name, dd_ref=d["ref"]["dd"], dd_auto=d["dd"], n_ref_cross=len(rc), ref_vessels=ref_v, mags={})
    for mag in MAGS:
        pf = Field("pred", name, mag=mag, gt=gt)
        pmask = pf.mask(box0)
        for c in rc:            # 专家截点、同一法向上量模型
            c.update({"p" + k: v for k, v in widths_multi(
                pf, c["x"], c["y"], c["nx"], c["ny"], max(3.0, 0.5 * c["w"]), min(60.0, 2 * c["chord"] + 10),
                THR_W, 1.5 * c["chord"] + 4).items()})
        paired = []
        for mem in rg:
            paired.append({"w@%g" % t: group_width(rc, mem, "pw@%g" % t)[0] for t in THR_W})
        pc_rr = crossings(pf, pmask, box0, fov, *ring_ref)
        pg_rr = group(pc_rr)
        mr, mp = set(), set()
        for gi, mem in enumerate(rg):
            for gj, pm in enumerate(pg_rr):
                if any(rc[i]["k"] == pc_rr[j]["k"] and ang_overlap(rc[i], pc_rr[j], 2.0 / rc[i]["r"])
                       for i in mem for j in pm):
                    mr.add(gi)
                    mp.add(gj)
        pc = crossings(pf, pmask, box0, fov, *ring_auto, av_gt=av_gt, av_pred=av_pred, thrs=THR_W)
        auto_v = []
        for mem in group(pc):
            v = {"w@%g" % t: group_width(pc, mem, "w@%g" % t)[0] for t in THR_W}
            v.update(gt=group_label(pc, mem, "gt"), lw=group_label(pc, mem, "pred"))
            auto_v.append(v)
        res["mags"][str(mag)] = dict(paired=paired, auto=auto_v,
                                     det=dict(ref=len(rg), found=sorted(mr), pred=len(pg_rr), pred_matched=len(mp)))
    return res


def apply_w(v, how):
    """how = ("thr", t) 或 ("lin", a, b, t)：取 / 校准一根血管的管径。"""
    w = v.get("w@%g" % (how[3] if how[0] == "lin" else how[1]))
    if w is None:
        return None
    return how[1] * w + how[2] if how[0] == "lin" else w


def idx_of(p, mag, src, how, label):
    """一张图的 CRAE / CRVE / AVR。src = "paired"（专家截点处量模型）或 "auto"（全自动）。"""
    m = p["mags"][mag]
    if src == "paired":
        vs = [(apply_w(v, how), r[label]) for v, r in zip(m["paired"], p["ref_vessels"])]
    else:
        vs = [(apply_w(v, how), v[label]) for v in m["auto"]]
    return summary_idx(vs)


def ref_idx(p):
    return summary_idx([(r["w"], r["gt"]) for r in p["ref_vessels"]])


def big6_pairs(p, mag, how):
    """每张图人工 A/V 下最大的 6 根动脉 + 6 根静脉（按参考管径）：(模型, 参考)。"""
    rv = p["ref_vessels"]
    out = []
    for lab in "AV":
        ii = sorted([i for i, r in enumerate(rv) if r["gt"] == lab], key=lambda i: -rv[i]["w"])[:6]
        out += [(apply_w(p["mags"][mag]["paired"][i], how), rv[i]["w"]) for i in ii]
    return out


def loio(per, mag, kind):
    """留一图：在其余 14 张上定量法（等值线 / 线性校准），用到留下的那张。返回每张图用的 how。"""
    hows = []
    for i in range(len(per)):
        tr = [p for j, p in enumerate(per) if j != i]
        if kind == "thr":
            def cost(t):
                e = 0.0
                for p in tr:
                    R, P = ref_idx(p), idx_of(p, mag, "paired", ("thr", t), "gt")
                    for k in ("crae", "crve"):
                        e += abs(P[k] - R[k]) / R[k] if P[k] and R[k] else 1.0
                return e
            hows.append(("thr", min(THR_W, key=cost)))
        else:
            pr = [(a, b) for p in tr for a, b in big6_pairs(p, mag, ("thr", -3)) if a is not None]
            x, y = np.array([a for a, _ in pr]), np.array([b for _, b in pr])
            A, B = np.polyfit(x, y, 1)
            hows.append(("lin", float(A), float(B), -3))
    return hows


def fmt_agree(a):
    return "n %d bias %+.2f MAE %.2f MAPE %.3f r %.2f ICC %.2f" % (
        a.get("n", 0), a.get("bias", np.nan), a.get("mae", np.nan), a.get("mape", np.nan), a.get("r", np.nan),
        a.get("icc", np.nan))


def cmd_measure(names):
    with Pool(int(os.environ.get("C4_JOBS", "12"))) as pool:
        per = pool.map(measure_one, names)
    S = {}
    refs = [ref_idx(p) for p in per]
    S["ref"] = dict(vessels_per_image=float(np.mean([len(p["ref_vessels"]) for p in per])),
                    nA_mean=float(np.mean([r["nA"] for r in refs])), nV_mean=float(np.mean([r["nV"] for r in refs])),
                    images_6A6V=int(sum(r["nA"] >= 6 and r["nV"] >= 6 for r in refs)),
                    crae_mean=float(np.mean([r["crae"] for r in refs])),
                    crve_mean=float(np.mean([r["crve"] for r in refs])),
                    avr_mean=float(np.mean([r["avr"] for r in refs])),
                    avr_sd=float(np.std([r["avr"] for r in refs], ddof=1)),
                    crae_sd=float(np.std([r["crae"] for r in refs], ddof=1)),
                    crve_sd=float(np.std([r["crve"] for r in refs], ddof=1)))
    lab = [(r["gt"], r["lw"], r["w"]) for p in per for r in p["ref_vessels"] if r["gt"] in "AV"]
    big = [t for p in per for t in sorted([(r["gt"], r["lw"], r["w"]) for r in p["ref_vessels"] if r["gt"] in "AV"],
                                          key=lambda t: -t[2])[:12]]
    S["av"] = dict(n=len(lab), acc=sum(g == q for g, q, _ in lab) / len(lab),
                   n_big12=len(big), acc_big12=sum(g == q for g, q, _ in big) / len(big),
                   ref_w_lw_labels={k: agree([summary_idx([(r["w"], r["lw"]) for r in p["ref_vessels"]])[k]
                                              for p in per], [r[k] for r in refs]) for k in ("crae", "crve", "avr")})
    for mag in MAGS:
        m = str(mag)
        s = {}
        dets = [p["mags"][m]["det"] for p in per]
        fw = [p["ref_vessels"][i]["w"] for p in per for i in p["mags"][m]["det"]["found"]]
        allw = [r["w"] for p in per for r in p["ref_vessels"]]
        s["det"] = dict(recall=len(fw) / len(allw), precision=sum(x["pred_matched"] for x in dets) / max(1, sum(
            x["pred"] for x in dets)))
        for lo, hi, tag in ((0, 6, "w<6"), (6, 12, "6-12"), (12, 99, ">=12")):
            s["det"]["recall_" + tag] = sum(lo <= w < hi for w in fw) / max(1, sum(lo <= w < hi for w in allw))
        plans = {"raw-3": [("thr", -3)] * len(per), "loio_thr": loio(per, m, "thr"), "loio_lin": loio(per, m, "lin")}
        for pk, hows in plans.items():
            if pk == "loio_thr":
                hw = sorted(set(h[1] for h in hows))
            elif pk == "loio_lin":
                hw = [float(np.mean([h[1] for h in hows])), float(np.mean([h[2] for h in hows]))]
            else:
                hw = -3
            q = dict(hows=hw)
            pv = [(apply_w(v, h), r["w"]) for p, h in zip(per, hows)
                  for v, r in zip(p["mags"][m]["paired"], p["ref_vessels"])]
            ok = [(a, b) for a, b in pv if a is not None]
            q["vessel_all"] = agree([a for a, _ in ok], [b for _, b in ok])
            q["vessel_missed"] = sum(a is None for a, _ in pv)
            for lo, hi, tag in ((0, 6, "w<6"), (6, 12, "6-12"), (12, 99, ">=12")):
                sel = [(a, b) for a, b in ok if lo <= b < hi]
                q["vessel_" + tag] = agree([a for a, _ in sel], [b for _, b in sel])
            b6 = [(a, b) for p, h in zip(per, hows) for a, b in big6_pairs(p, m, h) if a is not None]
            q["vessel_big6"] = agree([a for a, _ in b6], [b for _, b in b6])
            for V, src, labk in (("V1", "paired", "gt"), ("V2", "auto", "gt"), ("V3", "auto", "lw")):
                vals = [idx_of(p, m, src, h, labk) for p, h in zip(per, hows)]
                for k in ("crae", "crve", "avr"):
                    q["%s_%s" % (V, k)] = agree([v[k] if v[k] is not None else np.nan for v in vals],
                                                [r[k] for r in refs])
                q[V + "_per_image"] = vals
            s[pk] = q
        S[m] = s
    rep = dict(split="HRF TRAIN only (15)", zone="Zone B = 0.5-1 DD from the disc margin = 1.0-1.5 DD from the centre",
               reference="width on the HRF manual segmentation (manual1) with the same profile algorithm; "
                         "A/V from lwnet data/HRF/manual_av; disc = mean of the two experts (xls)",
               variants="V1 = model width at the expert crossings (manual A/V, expert disc) -> calibre only; "
                        "V2 = fully automatic vessels + widths on the program-19 ring, manual A/V; V3 = V2 with lwnet A/V",
               plans="raw-3 = width at the segmentation threshold -3; loio_thr = iso-logit chosen on the other 14 images "
                     "(min CRAE+CRVE relative error, V1); loio_lin = linear calibration of w@-3 fitted on the other 14 "
                     "images' six largest A + V",
               units="native HRF pixels (3504 x 2336); AVR unitless", ref_per_image=refs, summary=S, per_image=per)
    json.dump(rep, open(os.path.join(OUT, "measure.json"), "w"), indent=1)
    print("REF", json.dumps(S["ref"]))
    print("AV acc %.3f (n %d), big12 %.3f; ref widths + lwnet labels: CRAE %s | CRVE %s | AVR %s" % (
        S["av"]["acc"], S["av"]["n"], S["av"]["acc_big12"], fmt_agree(S["av"]["ref_w_lw_labels"]["crae"]),
        fmt_agree(S["av"]["ref_w_lw_labels"]["crve"]), fmt_agree(S["av"]["ref_w_lw_labels"]["avr"])))
    for mag in MAGS:
        s = S[str(mag)]
        print("==== m%s  det %s" % (mag, {k: round(v, 3) for k, v in s["det"].items()}))
        for pk in ("raw-3", "loio_thr", "loio_lin"):
            q = s[pk]
            print("  -- %s %s  missed %d" % (pk, q["hows"], q["vessel_missed"]))
            for k in ("vessel_all", "vessel_w<6", "vessel_6-12", "vessel_>=12", "vessel_big6"):
                print("     %-12s %s" % (k, fmt_agree(q[k])))
            for V in ("V1", "V2", "V3"):
                for k in ("crae", "crve", "avr"):
                    print("     %s %-4s %s" % (V, k, fmt_agree(q["%s_%s" % (V, k)])))



def cmd_board(names, mag=2, host="192.168.1.254"):
    """板上（部署的 v5.20，不改服务端、不往板上写文件）：同样的 Zone B 框按 JOB_TILE 跑，层从 /data/hrf 读；
    logit 与 PC 的整数参考逐位比，记耗时。"""
    import imagelink
    import jobs
    pyr = json.load(open(r"E:\PLD\tcp_image\tcp_image_ui\tile_pyramid.json", encoding="utf-8"))
    by_name = {c["name"]: c for c in pyr["cases"].values()}
    link = imagelink.ImageLink(host, 5060, timeout=30)
    rows = []
    try:
        caps = jobs.caps(link)
        assert "tile" in caps.get("jobs", []), caps
        for n in names:
            z = np.load(os.path.join(OUT, "logits_%s_m%s.npz" % (n, mag)))
            W, H = (int(v) for v in z["level"])
            lv = by_name[n]["levels"][str(mag)]
            assert (lv["w"], lv["h"]) == (W, H), (n, lv, W, H)
            box = tuple(int(v) for v in z["box"])
            t0 = time.perf_counter()
            out = te.run_board(link, W, H, box, te.TILE_LOGITS | te.TILE_MASK, path=lv["path"], thr=THR)
            wall = time.perf_counter() - t0
            res = out["result"]
            same = bool(res.get("ok")) and np.array_equal(out["logits"], z["logits"])
            unet_ms = [t.get("run_us") for t in out["tiles"]]
            rows.append(dict(name=n, box=box, ok=bool(res.get("ok")), logits_equal=same, tiles=len(out["tiles"]),
                             wall_s=round(wall, 3), job_wall_s=round(out["wall_s"], 3),
                             board_ms=res.get("ms", res.get("total_ms")), tile_unet_ms=unet_ms,
                             result={k: v for k, v in res.items() if not isinstance(v, (list, dict))}))
            print("%-6s box %s tiles %d equal %s wall %.3f s  %s" % (n, box, len(out["tiles"]), same, wall,
                                                                   rows[-1]["result"]), flush=True)
    finally:
        link.close()
    rep = dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), server=caps.get("server"), mag=mag,
               all_equal=all(r["logits_equal"] for r in rows), rows=rows,
               wall_s_mean=float(np.mean([r["wall_s"] for r in rows])))
    json.dump(rep, open(os.path.join(OUT, "board_m%s.json" % mag), "w"), indent=1, default=str)
    print("C4_BOARD_EQUAL" if rep["all_equal"] else "C4_BOARD_DIFF", "mean wall %.3f s" % rep["wall_s_mean"])


def cmd_ci(names, nboot=2000, seed=20261018):
    """按图重抽样（15 张，2000 次）给关键 ICC / MAE 的 95% 区间：每根血管（最大 6A + 6V）和每张图的 CRAE / CRVE / AVR。"""
    rep = json.load(open(os.path.join(OUT, "measure.json")))
    per, refs, S = rep["per_image"], rep["ref_per_image"], rep["summary"]
    rng = np.random.default_rng(seed)
    out = {}
    for mag in ("1", "2", "2.5"):
        for pk in ("raw-3", "loio_lin", "loio_thr"):
            q = S[mag][pk]
            if pk == "loio_lin":
                hows = loio(per, mag, "lin")
            elif pk == "loio_thr":
                hows = loio(per, mag, "thr")
            else:
                hows = [("thr", -3)] * len(per)
            b6 = [[(a, b) for a, b in big6_pairs(p, mag, h) if a is not None] for p, h in zip(per, hows)]
            res = {}
            for key, fn in (("vessel_big6_icc", lambda ii: agree([a for i in ii for a, _ in b6[i]],
                                                                 [b for i in ii for _, b in b6[i]])["icc"]),
                            ("vessel_big6_mape", lambda ii: agree([a for i in ii for a, _ in b6[i]],
                                                                  [b for i in ii for _, b in b6[i]])["mape"])):
                bs = [fn(rng.integers(0, len(per), len(per))) for _ in range(nboot)]
                res[key] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
            for V in ("V1", "V3"):
                vals = q[V + "_per_image"]
                for k in ("crae", "crve", "avr"):
                    p_ = np.array([v[k] if v[k] is not None else np.nan for v in vals], float)
                    r_ = np.array([r[k] for r in refs], float)
                    bs = []
                    for _ in range(nboot):
                        ii = rng.integers(0, len(per), len(per))
                        a = agree(p_[ii], r_[ii])
                        if "icc" in a:
                            bs.append(a["icc"])
                    res["%s_%s_icc" % (V, k)] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
            out["%s/%s" % (mag, pk)] = res
    json.dump(out, open(os.path.join(OUT, "ci.json"), "w"), indent=1)
    for k, v in out.items():
        print(k, {a: [round(x, 2) for x in b] for a, b in v.items()})


def cmd_fig(names, mag=2):
    """示意图（报告用）：Zone B 框内原图 60% 亮度；白 = 人工与模型都是血管，绿 = 只有人工，品红 = 只有模型；
    黄圈 = 0.5 / 1.0 / 1.5 DD（视盘边缘、Zone B 内、外缘，专家视盘）；青圈 = 程序 19 的视盘；
    短线 = 内外两圈上人工分割的截点法向宽度（红 = 人工 A/V 动脉，蓝 = 静脉），数字 = 外圈上的宽（像素）。"""
    from PIL import ImageDraw
    for name in names:
        d = discs()[name]
        gt, fov = load_gt(name)
        rgb = load_rgb(name)
        av_gt = np.asarray(Image.open(os.path.join(AV_GT, name + "_AVmanual.png")).convert("RGB"))
        z = np.load(os.path.join(OUT, "av_%s.npz" % name))
        box0 = tuple(int(v) for v in z["box0"])
        bx, by, bw, bh = box0
        rf, pf = Field("ref", name, gt=gt), Field("pred", name, mag=mag, gt=gt)
        pm = pf.mask(box0)
        cx, cy, dd = d["ref"]["cx"], d["ref"]["cy"], d["ref"]["dd"]
        rc = crossings(rf, gt[by:by + bh, bx:bx + bw], box0, fov, cx, cy, dd, av_gt=av_gt)
        im = rgb[by:by + bh, bx:bx + bw].astype(float) * 0.6
        rm = gt[by:by + bh, bx:bx + bw]
        im[rm & ~pm] = [0, 255, 0]
        im[pm & ~rm] = [255, 0, 255]
        im[rm & pm] = [255, 255, 255]
        I = Image.fromarray(im.clip(0, 255).astype(np.uint8))
        D = ImageDraw.Draw(I)
        for rr in (0.5 * dd, ZONE_IN * dd, ZONE_OUT * dd):
            D.ellipse([cx - rr - bx, cy - rr - by, cx + rr - bx, cy + rr - by], outline=(255, 255, 0))
        ax, ay, ar = d["cx"] - bx, d["cy"] - by, 0.5 * d["dd"]
        D.ellipse([ax - ar, ay - ar, ax + ar, ay + ar], outline=(0, 200, 255))
        for x in rc:
            if x["k"] not in (0, N_CIRC - 1):
                continue
            h, X, Y = x["w"] / 2, x["x"] - bx, x["y"] - by
            col = (255, 60, 60) if x.get("gtA", 0) > x.get("gtV", 0) else (80, 120, 255)
            D.line([X - h * x["nx"], Y - h * x["ny"], X + h * x["nx"], Y + h * x["ny"]], fill=col, width=2)
            if x["k"] == N_CIRC - 1:
                D.text((X + 8, Y), "%.0f" % x["w"], fill=col)
        p = os.path.join(OUT, "fig_%s_m%s.png" % (name, mag))
        I.save(p)
        print(p)


def main():
    os.makedirs(OUT, exist_ok=True)
    cmd = sys.argv[1]
    names = sys.argv[sys.argv.index("--names") + 1].split(",") if "--names" in sys.argv else list(TRAIN)
    guard(names)
    {"disc": cmd_disc, "tiles": cmd_tiles, "av": cmd_av, "measure": cmd_measure, "board": cmd_board,
     "ci": cmd_ci, "fig": cmd_fig}[cmd](names)


if __name__ == "__main__":
    main()
