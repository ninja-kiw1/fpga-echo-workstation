# -*- coding: utf-8 -*-
"""verify_board_v516.py —— 服务端 v5.16 + r2 固件的新功能上板验收（P1 1.1 / 1.2 / 1.4）。

  caps     板端报告 v5.16、vis = true、jobs 里有 render3d / overlay
  reg      配准 PL 引擎（engine 1）vs U-Net 老路（engine 0）：同一块板、同样的帧，(dx, dy)、路径、每个候选的代价（double）完全相同；
           STARE 已知平移（verify_board_jobs.reg_frames）；记录两条路的耗时
  reg_q    256 档、1/4 像素：板上结果 = ai/vis_hls/visref.search（逐位：最优位移、路径、每个候选的 SAD / 重叠面积 -> MAD）
  ovl      JOB_OVERLAY：PL（vis_layer）= PS 回落实现（force_ps）= visref.overlay，逐字节；眼底叠加 / 热力图还和 confidence.py 比
  r3d      JOB_RENDER3D：板上渲染 vs PC 的 ct_volume.Surface（浮点，容差：前景像素 xor、像素差统计；表面点数应相同）
  ct3d     ct_seg 带 CT_RENDER3D（spleen_10）：分割结果照旧（体积与不带时相同），附 render3d 图块
只用验证 / 训练数据（CT 测试集 spleen_8/9/13/17/26/32/63 不碰）。srtp-vision：
    python verify_board_v516.py [--host 192.168.1.254] [--out board_check_v5/v516_acceptance.json]
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, "..", "tcp_image_rx"))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(RX, "ai", "vis_hls"))
import numpy as np  # noqa: E402

import confidence  # noqa: E402
import ct_volume  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import visref as V  # noqa: E402
from verify_board_jobs import MSD, reg_frames  # noqa: E402

TAB = np.array(json.load(open(os.path.join(RX, "ai", "vis_hls", "absdiff_table.json")))["table"], np.int64)


def check_caps(link):
    link.connect()
    c = jobs.caps(link)
    ok = c.get("server") == "v5.16" and c.get("vis") is True and "render3d" in c["jobs"] and "overlay" in c["jobs"]
    return dict(passed=ok, caps=c)


def check_reg(link, n_pairs=30):
    rows, bad = [], []
    for i, (a, b, truth) in enumerate(reg_frames(n_pairs)):
        for mode in ((0,) if i % 5 else (0, 1)):
            r = {}
            for eng in (0, 1):
                link.connect()
                r[eng] = jobs.reg_search(link, a, b, mode=mode, radius=7, engine=eng)
            same = (r[1]["engine"] == "vis" and r[0]["engine"] == "unet" and (r[0]["dx"], r[0]["dy"]) == (r[1]["dx"], r[1]["dy"])
                    and r[0]["costs"] == r[1]["costs"] and r[0]["mad"] == r[1]["mad"]
                    and (mode == 1 or r[0]["path"] == r[1]["path"]))
            rows.append(dict(pair=i, mode=mode, truth=truth, best=(r[1]["dx"], r[1]["dy"]), same=same,
                             unet_ms=r[0]["total_ms"], vis_ms=r[1]["total_ms"], vis_pl_ms=r[1]["pl_ms"], evals=r[1]["evals"]))
            if not same:
                bad.append(rows[-1])
    rec = sum(r["best"] == r["truth"] for r in rows) / float(len(rows))
    t = lambda k, m: float(np.median([r[k] for r in rows if r["mode"] == m]))
    return dict(passed=not bad, runs=len(rows), recovered=rec, bad=bad[:5],
                ms=dict(tss_unet=t("unet_ms", 0), tss_vis=t("vis_ms", 0), full_unet=t("unet_ms", 1), full_vis=t("vis_ms", 1),
                        tss_vis_pl=t("vis_pl_ms", 0), full_vis_pl=t("vis_pl_ms", 1)))


def check_reg_q(link):
    rng = np.random.default_rng(16)
    from PIL import Image
    g = np.asarray(Image.open(os.path.join(HERE, "samples", "im0003.ppm")).convert("RGB").resize((256, 256)))[..., 1]
    g = np.ascontiguousarray(g)
    cases, bad = [], []
    for k in range(6):
        n = 256 if k % 2 else 128
        src = g if n == 256 else np.ascontiguousarray(g[64:192, 64:192])
        d = (int(rng.integers(-5, 6)), int(rng.integers(-5, 6)))
        b = np.ascontiguousarray(np.roll(src, (-d[1], -d[0]), (0, 1)))
        mode, steps, sub = (0, (8, 4, 2, 1) if n == 256 else (4, 2, 1), 2) if k < 4 else (1, (4, 2, 1), 1)
        link.connect()
        j = jobs.reg_search(link, src.tobytes(), b.tobytes(), mode=mode, radius=6, steps=steps, engine=1, subpel=sub, size=n)
        ref = V.search(src, b, TAB, mode, 6, steps, sub)
        costs_ref = {(q[0], q[1]): q[2] / float(q[3]) for q in ref["records"]}
        same = j.get("engine") == "vis" and (j["qx"], j["qy"]) == ref["best"] and [tuple(p) for p in j["path_q"]] == ref["path"] \
            and j["costs_q"] == costs_ref
        cases.append(dict(n=n, mode=mode, subpel=sub, true_q=(4 * d[0], 4 * d[1]), found_q=(j["qx"], j["qy"]), same=same,
                          pl_ms=j["pl_ms"], evals=j["evals"]))
        if not same:
            bad.append(cases[-1])
    return dict(passed=not bad, cases=cases)


def check_ovl(link):
    import imageio as uiio
    rows, bad = [], []
    heat = np.array(confidence._HEAT_LUT, np.uint8)
    raw, _ = uiio.load_image(os.path.join(HERE, "samples", "im0001.ppm"))
    base = np.frombuffer(raw, np.uint8).reshape(512, 512, 3)
    q = np.load(os.path.join(RX, "ai", "vis_hls", "board_check_fundus_q.npy")) if os.path.exists(
        os.path.join(RX, "ai", "vis_hls", "board_check_fundus_q.npy")) else None
    rng = np.random.default_rng(5)
    if q is None:
        q = rng.integers(-20, 20, (512, 512)).astype(np.int8)
    plane = (np.asarray(q, np.int64) + 128).astype(np.uint8).tobytes()
    cases = [("fundus_overlay", 512, 512, jobs.OVL_BASE_RGB | jobs.OVL_HAS_LOGIT | jobs.OVL_FILL, dict(base=base, logits=q, thr=-3),
              np.frombuffer(confidence.overlay_rgb(raw, plane, -3), np.uint8).reshape(512, 512, 3)),
             ("fundus_heat", 512, 512, jobs.OVL_BASE_HEAT | jobs.OVL_HAS_LOGIT, dict(logits=q, lut=heat),
              np.frombuffer(confidence.heat_rgb(plane), np.uint8).reshape(512, 512, 3)),
             ("fundus_unsure", 512, 512, jobs.OVL_BASE_RGB | jobs.OVL_HAS_LOGIT | jobs.OVL_FILL | jobs.OVL_UNSURE,
              dict(base=base, logits=q, thr=-3, band=3), np.frombuffer(confidence.uncertain_rgb(raw, plane, -3, 3), np.uint8).reshape(512, 512, 3))]
    q128 = rng.integers(-30, 30, (128, 128)).astype(np.int8)
    m = rng.random((512, 512)) < 0.3
    grey = rng.integers(0, 256, (512, 512)).astype(np.uint8)
    cases.append(("ct_style", 512, 512, jobs.OVL_BASE_GREY | jobs.OVL_HAS_BITS | jobs.OVL_FILL_FROM_BITS | jobs.OVL_FILL | jobs.OVL_CONTOUR,
                  dict(base=grey, bits=m, fill=(0, 255, 0), fill_a=6, contour=(255, 255, 0)), None))
    cases.append(("heat128", 512, 512, jobs.OVL_BASE_HEAT | jobs.OVL_HAS_LOGIT | jobs.OVL_CONTOUR, dict(logits=q128, lut=heat, thr=1), None))
    for name, ow, oh, fl, kw, golden in cases:
        link.connect()
        pl, rp = jobs.overlay(link, ow, oh, fl, **kw)
        link.connect()
        ps, rs = jobs.overlay(link, ow, oh, fl, force_ps=True, **kw)
        ref = V.overlay(ow, oh, fl, kw.get("base"), kw.get("logits"), kw.get("bits"), kw.get("lut"), kw.get("thr", 0), kw.get("band", 0),
                        kw.get("fill", (255, 0, 0)), kw.get("fill_a", 16), kw.get("unsure", (255, 200, 0)), kw.get("unsure_a", 16),
                        kw.get("contour", (0, 255, 0)))
        same = rp["engine"] == "vis" and rs["engine"] == "ps" and np.array_equal(pl, ref) and np.array_equal(ps, ref) and \
            (golden is None or np.array_equal(pl, golden))
        rows.append(dict(case=name, same=same, pl_ms=rp["pl_ms"], vis_total_ms=rp["ms"], ps_total_ms=rs["ms"], calls=rp["calls"]))
        if not same:
            bad.append(name)
    return dict(passed=not bad, rows=rows)


def check_r3d(link, names=("spleen_10", "spleen_25", "spleen_47")):
    rows = []
    angles = [0.0, 45.0, 90.0, 200.0]
    for nm in names:
        v = ct_volume.load_ct(os.path.join(MSD, nm + ".nii.gz"))
        m = v["label"]
        link.connect()
        views, info = jobs.render3d(link, m, v["spacing"], angles)
        s = ct_volume.Surface(m, v["spacing"])
        stat = []
        for a, img in zip(angles, views):
            r = s.render(a, 320).astype(np.int64)
            d = np.abs(img.astype(np.int64) - r)
            fr, fb = np.any(r != 24, -1), np.any(img != 24, -1)
            stat.append(dict(angle=a, px_differ=int(np.any(d > 0, -1).sum()), max_abs=int(d.max()), fg=int(fr.sum()),
                             fg_xor=int((fr ^ fb).sum())))
        surf = info.get("surface", {})
        rows.append(dict(case=nm, points_board=surf.get("points"), points_pc=int(len(s.p[0])), build_ms=surf.get("build_ms"),
                         render_ms=surf.get("render_ms"), views=stat))
    worst_xor = max(x["fg_xor"] / max(1, x["fg"]) for r in rows for x in r["views"])
    ok = all(r["points_board"] == r["points_pc"] for r in rows) and worst_xor < 0.01
    return dict(passed=ok, worst_fg_xor_fraction=worst_xor, rows=rows)


def check_ct3d(link):
    p = os.path.join(MSD, "spleen_10.nii.gz")
    link.connect()
    a = jobs.ct_seg(link, p, flags=jobs.CT_MASK_FINAL)
    link.connect()
    b = jobs.ct_seg(link, p, flags=jobs.CT_MASK_FINAL | jobs.CT_RENDER3D, views=12)
    views = jobs.unpack_render3d(b)
    ok = a["result"]["volume_ml"] == b["result"]["volume_ml"] and a["blobs"]["mask_final"] == b["blobs"]["mask_final"] and len(views) == 12
    return dict(passed=ok, volume_ml=b["result"]["volume_ml"], views=len(views), surface=b["result"].get("surface"),
                total_ms=[a["result"]["ms"]["total"], b["result"]["ms"]["total"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "v516_acceptance.json"))
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    link = imagelink.ImageLink(a.host, a.port, timeout=30.0)
    rep, t0 = {}, time.time()
    for name, fn in (("caps", check_caps), ("reg", check_reg), ("reg_q", check_reg_q), ("ovl", check_ovl), ("r3d", check_r3d),
                     ("ct3d", check_ct3d)):
        if a.only and name not in a.only:
            continue
        rep[name] = fn(link)
        print(name, json.dumps({k: v for k, v in rep[name].items() if k not in ("rows", "cases", "caps")}, ensure_ascii=False), flush=True)
    rep["pass"] = all(v["passed"] for v in rep.values() if isinstance(v, dict))
    rep["seconds"] = time.time() - t0
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False, default=float)
    print("V516_BOARD_PASS" if rep["pass"] else "V516_BOARD_FAIL")
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
