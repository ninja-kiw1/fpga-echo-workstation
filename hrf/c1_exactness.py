# -*- coding: utf-8 -*-
"""c1_exactness.py —— 部分 C 第一步的核心证明：分块拼接 == 整幅图一次推理，逐位相同。

对每一"层"（原图按比例缩放、补到 8 的倍数，见 tile_engine.make_level）：
  1. 参照 R = 整幅层图一次跑整网（tile_ref.Ref(limit=False)：HLS 算子的 NumPy 逐位参考、去掉 512 行宽上限）；
  2. 在层上取很多框（整幅、随机大小 / 位置、贴边、跨块缝、> 512 的多块框），按 tile_engine.plan 切块，
     每块用 Ref(limit=True)（= 板上 HLS 核的尺寸限制，块都 <= 512）算，取自有区拼回；
  3. 框内每个算过的像素与 R 逐位比；视野跳过的块要求自有区里确实没有视野像素。
另外：
  * 512 帧上 2000 个随机框：plan 只出一块，且等于 proto.roi_crop（老 ROI = 1 倍框选的特例）；
  * 原始分辨率 HRF（3504x2336）上没有整幅参照（内存），改查"两种不同的切法在重叠处逐位相同"。

用法：python c1_exactness.py [--quick] [--jobs N]   结果写 results/c1_exactness.json
"""
import json
import os
import random
import sys
import time
from multiprocessing import Pool

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"E:\PLD\tcp_image\tcp_image_ui")
import tile_engine as te          # noqa: E402

STARE = r"E:\PLD\medical_fpga\data\stare\png\images"
HRF = os.path.join(HERE, "all", "images")
SPLIT = json.load(open(os.path.join(HERE, "split.json"), encoding="utf-8"))
OUT = os.path.join(HERE, "results")


def load_rgb(path):
    return np.asarray(Image.open(path).convert("RGB"))


def boxes_for(W, H, rnd, n_random):
    bs = [(0, 0, W, H),                                  # 整幅
          (0, 0, min(W, 100), min(H, 80)),               # 贴左上
          (W - min(W, 130), H - min(H, 90), min(W, 130), min(H, 90)),   # 贴右下
          (max(0, W // 2 - 300), max(0, H // 2 - 20), min(W, 600), 40),  # 横跨块缝
          (max(0, W // 2 - 233), 0, min(W, 466), H)]     # 正好跨两块的竖条
    for _ in range(n_random):
        w = rnd.randrange(8, min(W, 1400) + 1)
        h = rnd.randrange(8, min(H, 1000) + 1)
        bs.append((rnd.randrange(0, W - w + 1), rnd.randrange(0, H - h + 1), w, h))
    return bs


def check_level(job):
    """一层：整幅参照 + 若干框分块比对。在子进程里跑。"""
    from tile_ref import Ref
    name, path, s, n_random, seed = job
    rgb0 = load_rgb(path)
    lvl, valid = te.make_level(rgb0, s)
    H, W = lvl.shape[:2]
    ref_whole, ref_tile = Ref(limit=False, max_cache=2), Ref(limit=True, max_cache=12)
    t0 = time.perf_counter()
    R = ref_whole.run(lvl)
    t_whole = time.perf_counter() - t0
    fov = te.fov_mask(lvl)
    rnd = random.Random(seed)
    res = dict(name=name, scale=s, level=[W, H], valid=list(valid), whole_s=round(t_whole, 1), boxes=[])
    cache = {}

    def runner(rgb):
        key = rgb.tobytes()
        if key not in cache:
            cache[key] = ref_tile.run(rgb)
        return cache[key]

    for box in boxes_for(W, H, rnd, n_random):
        for skip in (False, True):
            t1 = time.perf_counter()
            out, tiles = te.run_tiles(lvl, box, runner, skip_fov=skip)
            x, y, w, h = box
            Rb = R[y:y + h, x:x + w]
            computed = np.ones((h, w), bool)
            fov_lost = 0
            for t in tiles:
                if t.skip:
                    computed[t.oy - y:t.oy - y + t.oh, t.ox - x:t.ox - x + t.ow] = False
                    fov_lost += int(fov[t.oy:t.oy + t.oh, t.ox:t.ox + t.ow].sum())
            bad = int((out[computed] != Rb[computed]).sum())
            c = te.costs(tiles)
            res["boxes"].append(dict(box=list(box), fov_skip=skip, tiles=c["tiles"], run=c["run"],
                                     skipped=c["skipped"], computed_px=int(computed.sum()), mismatches=bad,
                                     fov_px_in_skipped=fov_lost, halo_overhead=round(c["halo_overhead"], 4),
                                     sec=round(time.perf_counter() - t1, 1)))
    ref_whole.close()
    ref_tile.close()
    res["ok"] = all(b["mismatches"] == 0 and b["fov_px_in_skipped"] == 0 for b in res["boxes"])
    return res


def check_overlap_native(job):
    """原始分辨率：同一区域两种切法（框起点错开 136 像素）在重叠处逐位相同。"""
    from tile_ref import Ref
    name, path, region = job
    rgb0 = load_rgb(path)
    lvl, _ = te.make_level(rgb0, 1.0)
    ref = Ref(limit=True, max_cache=12)
    x, y, w, h = region
    a, _ = te.run_tiles(lvl, (x, y, w, h), ref.run, skip_fov=False)
    b, tb = te.run_tiles(lvl, (x + 136, y + 104, w - 136, h - 104), ref.run, skip_fov=False)
    bad = int((a[104:, 136:] != b).sum())
    ref.close()
    return dict(name=name, region=list(region), compared_px=int(b.size), tiles_b=len(tb), mismatches=bad)


def check_negative(job):
    """反例：边距只留 16（< 22）时，多块框的拼接必须出现差异（证明比对确实敏感）。"""
    from tile_ref import Ref
    name, path, s = job
    lvl, _ = te.make_level(load_rgb(path), s)
    H, W = lvl.shape[:2]
    R = Ref(limit=False, max_cache=1).run(lvl)
    ref = Ref(limit=True, max_cache=12)
    box = (0, 0, W, H)
    xs = te.plan_axis(0, W, W, margin=16)
    ys = te.plan_axis(0, H, H, margin=16)
    tiles = [te.Tile(i, tx, ty, tw, th, ox0, oy0, ox1 - ox0, oy1 - oy0) for i, ((ty, th, oy0, oy1), (tx, tw, ox0, ox1))
             in enumerate([(a, b) for a in ys for b in xs])]
    owns = [te.own_of(t, ref.run(te.tile_rgb(lvl, t))) for t in tiles]
    bad = int((te.stitch(box, tiles, owns) != R).sum())
    return dict(name=name, margin=16, tiles=len(tiles), mismatches=bad)


def check_roi_special():
    """512 帧：任意框只出一块，且 = proto.roi_crop（老 ROI 规则）。"""
    import proto
    rnd = random.Random(20261018)
    bad = 0
    n = 0
    for _ in range(2000):
        w, h = rnd.randrange(1, 513), rnd.randrange(1, 513)
        x, y = rnd.randrange(0, 513 - w), rnd.randrange(0, 513 - h)
        ts = te.plan((x, y, w, h), 512, 512)
        t = ts[0]
        ok = (len(ts) == 1 and (t.tx, t.ty, t.tw, t.th) == tuple(proto.roi_crop(x, y, w, h))
              and (t.ox, t.oy, t.ow, t.oh) == (x, y, w, h))
        bad += not ok
        n += 1
    return dict(boxes=n, mismatches=bad)


def main():
    quick = "--quick" in sys.argv
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 8
    os.makedirs(OUT, exist_ok=True)
    report = dict(roi_special=check_roi_special())
    print("512 帧单块 = proto.roi_crop：%(boxes)d 个框，不符 %(mismatches)d" % report["roi_special"])

    work = []
    stare = ["im0002", "im0077"] if quick else ["im0002", "im0077", "im0163", "im0319"]
    for i, nm in enumerate(stare):
        p = os.path.join(STARE, nm + ".png")
        w0 = Image.open(p).size[0]
        for k, lw in enumerate((512, 760, 1400) if not quick else (760,)):
            work.append(("stare/%s" % nm, p, lw / float(w0), 3 if quick else 6, 1000 * i + k))
    hrf = SPLIT["train"][:1] if quick else SPLIT["train"][:3]
    for i, nm in enumerate(hrf):
        p = os.path.join(HRF, SPLIT["files"][nm]["image"])
        for k, lw in enumerate((520, 1040, 2080) if not quick else (1040,)):
            work.append(("hrf/%s" % nm, p, lw / 3504.0, 3 if quick else 6, 5000 + 1000 * i + k))
    native = [("hrf/%s" % nm, os.path.join(HRF, SPLIT["files"][nm]["image"]), (1200, 700, 1500, 1000))
              for nm in (SPLIT["train"][:1])]

    t0 = time.perf_counter()
    with Pool(jobs) as pool:
        lv = pool.map_async(check_level, work)
        nv = pool.map_async(check_overlap_native, native)
        ng = pool.map_async(check_negative, [(work[0][0], work[0][1], 1040 / 700.0)])
        report["levels"] = lv.get()
        report["native_overlap"] = nv.get()
        report["negative_margin16"] = ng.get()
    report["wall_s"] = round(time.perf_counter() - t0, 1)
    nbox = sum(len(r["boxes"]) for r in report["levels"])
    nbad = sum(b["mismatches"] for r in report["levels"] for b in r["boxes"])
    npx = sum(b["computed_px"] for r in report["levels"] for b in r["boxes"])
    for r in report["levels"]:
        mt = max(b["tiles"] for b in r["boxes"])
        print("  %-14s 层 %4dx%-4d 整幅 %5.1f s  框 %2d 个（最多 %2d 块） 不一致 %d  %s"
              % (r["name"], r["level"][0], r["level"][1], r["whole_s"], len(r["boxes"]), mt,
                 sum(b["mismatches"] for b in r["boxes"]), "PASS" if r["ok"] else "FAIL"))
    for r in report["native_overlap"]:
        print("  原始分辨率 %s 两种切法重叠 %d 像素，不一致 %d" % (r["name"], r["compared_px"], r["mismatches"]))
    for r in report["negative_margin16"]:
        print("  反例（边距 16）%s %d 块：不一致 %d（应 > 0）" % (r["name"], r["tiles"], r["mismatches"]))
    ok = (report["roi_special"]["mismatches"] == 0 and all(r["mismatches"] > 0 for r in report["negative_margin16"]) and all(r["ok"] for r in report["levels"])
          and all(r["mismatches"] == 0 for r in report["native_overlap"]))
    report["summary"] = dict(levels=len(report["levels"]), boxes=nbox, compared_px=npx, mismatches=nbad, ok=ok)
    json.dump(report, open(os.path.join(OUT, "c1_exactness%s.json" % ("_quick" if quick else "")), "w"), indent=1)
    print("层 %d 个、框 %d 个（含视野跳过 / 不跳过两种）、比对 %d 像素、不一致 %d；%.0f s"
          % (len(report["levels"]), nbox, npx, nbad, report["wall_s"]))
    print("C1_EXACTNESS_PASS" if ok else "C1_EXACTNESS_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
