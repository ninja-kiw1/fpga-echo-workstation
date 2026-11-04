# -*- coding: utf-8 -*-
"""verify_board_jobs.py —— 病例任务（v7 "HM"，板端整病例流程）上板验收。

每一项都和"老路"逐位比对：老路 = 紧凑帧逐帧发给同一块板子（logit 早已和 HLS 参考逐位一致）+ PC 上的
ct_volume.py / registration.py（和离线评测逐字节一致）。

  caps   板端报告支持的任务
  reg    F5 配准：板上三步 / 全搜索 vs registration.Registrar 走紧凑帧，(dx, dy)、路径、每个候选的代价完全相同；
         已知平移的恢复率
  ct     CT 整卷：4x4 块平均、粗分割 logit（vs 紧凑帧逐层）、粗掩膜（vs postprocess bilinear+lcc）、ROI 框、
         精分割输入（vs roi_payloads）、精分割 logit、最终掩膜（vs roi_combine）、体积（浮点完全相同）；
         放大默认在 PL 的 pp_layer 上（v5.14 起，要求每层都由 PL 放大）；一卷再用 PS 工作线程放大（flags bit3）跑一遍，
         结果必须一样；记录耗时 vs 紧凑帧流式

只用验证集和训练集的病例（测试集 spleen_8/9/13/17/26/32/63 不碰）。
用 srtp-vision 的 python（numpy + scipy）：
    python verify_board_jobs.py [--host 192.168.1.254] [--port 5060] [--quick] [--out board_check_v5/jobs_acceptance.json]
"""
import argparse
import json
import os
import random
import sys
import time
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np  # noqa: E402

import ct_volume  # noqa: E402
import imagelink  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402
import registration as reg  # noqa: E402

MSD = os.path.normpath(os.path.join(HERE, "..", "..", "Task09_Spleen", "Task09_Spleen", "imagesTr"))
VAL = ["spleen_10", "spleen_25", "spleen_41", "spleen_47", "spleen_52", "spleen_56", "spleen_59"]
TRAIN_EXTRA = ["spleen_12", "spleen_16", "spleen_53", "spleen_27"]     # 1.5 / 8.0 / 3.0 / 4.0 mm, 168 层、55 MB
TEST = {"spleen_8", "spleen_9", "spleen_13", "spleen_17", "spleen_26", "spleen_32", "spleen_63"}


def stream(link, payloads, model, w=128, kind=proto.V6C_KIND_RGB, flags=proto.FLAG_STATS | proto.FLAG_C_LOGIT):
    """老路：紧凑帧逐帧发（2 帧在途），回 (logit 列表, 统计块列表, 秒)。"""
    out, sts = [None] * len(payloads), [None] * len(payloads)
    link.connect()                                  # 板端 10 s 空闲超时：每段流之前重连
    t0 = time.perf_counter()
    q, k = deque(), 0
    while k < len(payloads) or q:
        while len(q) < 2 and k < len(payloads):
            link.send_v6c(payloads[k], w, kind=kind, model0=model, thr=1, flags=flags)
            q.append(k)
            k += 1
        i = q.popleft()
        st, lg, _ = link.recv_v6c(flags, w)
        if st is None or st.get("model") != model:
            raise RuntimeError("stream: board answered model %s, expected %d" % (st and st.get("model"), model))
        out[i], sts[i] = lg, st
    return out, sts, time.perf_counter() - t0


# ------------------------------------------------------------------ F5 配准
def reg_frames(n_pairs, seed=20261013):
    """STARE 样例图降到 256 灰度，A = (oy, ox) 处 128 块，B = 已知平移后的块：B(y - dy, x - dx) = A(y, x)。"""
    from PIL import Image
    rng = random.Random(seed)
    imgs = []
    for f in sorted(os.listdir(os.path.join(HERE, "samples"))):
        if f.endswith(".ppm"):
            g = np.asarray(Image.open(os.path.join(HERE, "samples", f)).convert("RGB").resize((256, 256)))[..., 1]
            imgs.append(g)
    pairs = []
    for i in range(n_pairs):
        g = imgs[i % len(imgs)]
        dx, dy = rng.randint(-7, 7), rng.randint(-7, 7)
        oy, ox = rng.randint(16, 112), rng.randint(16, 112)
        a = g[oy:oy + 128, ox:ox + 128]
        b = g[oy - dy:oy - dy + 128, ox - dx:ox - dx + 128]       # B(u, v) = A(u + dy, v + dx)
        pairs.append((a.tobytes(), b.tobytes(), (-dx, -dy)))           # registration 的约定：G(y, x) = B(y - dy, x - dx)
    return pairs


def check_reg(link, quick):
    pairs = reg_frames(8 if quick else 30)
    flags = proto.FLAG_STATS | proto.FLAG_REG

    def send(frame):
        link.send_v6c(frame, 512, kind=proto.V6C_KIND_RGB, model0=reg.MODEL, thr=8, flags=flags)
        st, _, _ = link.recv_v6c(flags, 512)
        return st["reg"]["sums"], st

    rows, bad = [], []
    for i, (a, b, truth) in enumerate(pairs):
        for mode in ((0,) if i % 5 else (0, 1)):              # 每 5 对做一次全搜索（225 个候选 = 15 帧）
            link.connect()
            t0 = time.perf_counter()
            r = reg.Registrar(a, b, send)
            if mode == 0:
                best, path = r.three_step()
            else:
                best, path = r.full(7), None
            t_pc = time.perf_counter() - t0
            j = jobs.reg_search(link, a, b, mode=mode, radius=7)
            same = (j["ok"] and (j["dx"], j["dy"]) == best and j["costs"] == r.cost and j["frames"] == r.frames
                    and (path is None or [tuple(p) for p in j["path"]] == path))
            rows.append(dict(pair=i, mode=mode, truth=truth, pc=best, board=(j.get("dx"), j.get("dy")), same=same,
                             frames=j.get("frames"), pc_s=round(t_pc, 4), job_s=round(j["wall_s"], 4),
                             board_ms=j.get("total_ms"), pl_ms=j.get("pl_ms")))
            if not same:
                bad.append(rows[-1])
            print("  reg pair %2d mode %d truth %-8s pc %-8s board %-8s %s  pc %.0f ms / job %.0f ms"
                  % (i, mode, truth, best, (j.get("dx"), j.get("dy")), "OK" if same else "DIFF",
                     t_pc * 1000, j["wall_s"] * 1000), flush=True)
    rec = sum(1 for r in rows if r["mode"] == 0 and tuple(r["board"]) == r["truth"])
    n3 = sum(1 for r in rows if r["mode"] == 0)
    return dict(pass_=not bad, rows=rows, mismatches=bad, three_step_recovered=rec, three_step_pairs=n3,
                pc_ms_mean=1000 * float(np.mean([r["pc_s"] for r in rows if r["mode"] == 0])),
                job_ms_mean=1000 * float(np.mean([r["job_s"] for r in rows if r["mode"] == 0])))


# ------------------------------------------------------------------ CT 整卷
def check_ct(link, name, pl=True, compare_stream=True):
    assert name not in TEST
    path = os.path.join(MSD, name + ".nii.gz")
    flags = jobs.CT_LOGITS | jobs.CT_MASK_COARSE | jobs.CT_MASK_FINAL | jobs.CT_INPUTS | (0 if pl else jobs.CT_PS_UPSAMPLE)
    link.connect()
    r = jobs.ct_seg(link, path, flags=flags)
    res = r["result"]
    if not res.get("ok"):
        return dict(name=name, pass_=False, error=res.get("error"))
    t0 = time.perf_counter()                        # 老路的 PC 准备：读文件、窗宽窗位、编码
    vol = ct_volume.load_ct(path)
    u8 = ct_volume.window_u8(vol["hu"])
    nz, sp = len(u8), vol["spacing"]
    pls = ct_volume.payloads(u8, sp[2], "z3")
    t_prep = time.perf_counter() - t0
    dz = sp[2]
    o = ct_volume.neighbour_offset(dz)
    checks = {}
    checks["nz"] = res["nz"] == nz
    checks["spacing"] = tuple(res["spacing"]) == tuple(sp)
    checks["offset"] = res["offset"] == o
    small = np.frombuffer(r["blobs"]["small"], np.uint8).reshape(nz, 128, 128)
    checks["box4"] = bool((small == ct_volume.box(u8, 4)).all())
    lc = np.frombuffer(r["blobs"]["logits_coarse"], np.int8).reshape(nz, 128, 128)
    out = dict(name=name, nz=nz, dz=dz, offset=o, pl_upsample=pl)
    t_stream = 0.0
    if compare_stream:
        ls, _, t = stream(link, pls, 14)
        t_stream += t
        checks["logits_coarse_vs_stream"] = bool((np.frombuffer(b"".join(ls), np.int8).reshape(nz, 128, 128) == lc).all())
    t0 = time.perf_counter()
    m0 = ct_volume.postprocess(lc, "bilinear+lcc")
    t_post = time.perf_counter() - t0
    mc = jobs.unpack_mask(r["blobs"]["mask_coarse"], (nz, 512, 512))
    checks["mask_coarse"] = bool((mc == m0).all())
    win = ct_volume.roi_window(m0, dz)
    checks["win"] = (res["win"] is None and win is None) or (res["win"] is not None and tuple(res["win"]) == tuple(win))
    if win is not None:
        t0 = time.perf_counter()
        pf = ct_volume.roi_payloads(u8, m0, win, "roi_prior", dz)
        t_post += time.perf_counter() - t0
        checks["inputs_fine"] = r["blobs"].get("inputs_fine") == b"".join(pf)
        lf = np.frombuffer(r["blobs"]["logits_fine"], np.int8).reshape(len(pf), 128, 128)
        if compare_stream:
            ls, _, t = stream(link, pf, 15)
            t_stream += t
            checks["logits_fine_vs_stream"] = bool((np.frombuffer(b"".join(ls), np.int8).reshape(lf.shape) == lf).all())
        t0 = time.perf_counter()
        final = ct_volume.roi_combine(lf, win, nz)
        t_post += time.perf_counter() - t0
    else:
        final = m0
    mf = jobs.unpack_mask(r["blobs"]["mask_final"], (nz, 512, 512))
    checks["mask_final"] = bool((mf == final).all())
    ml = ct_volume.volume_ml(final, sp)
    checks["volume_ml"] = res["volume_ml"] == ml
    checks["voxels"] = res["voxels"] == int(final.sum())
    checks["upsample_where"] = (res["upsample"]["pl"] == res["upsample"]["all"]) if pl else res["upsample"]["pl"] == 0
    prod = None
    if compare_stream:                              # 界面实际用的 flags（只要最终掩膜）：再跑一次计时，结果也要一样
        link.connect()
        rp = jobs.ct_seg(link, path, flags=jobs.CT_MASK_FINAL)
        checks["production_run_same"] = (rp["result"].get("volume_ml") == res["volume_ml"] and
                                         rp["blobs"].get("mask_final") == r["blobs"]["mask_final"])
        prod = dict(job_s=round(rp["wall_s"], 3), upload_s=round(rp["upload_s"], 3), board_ms=rp["result"].get("ms"))
    out.update(checks=checks, pass_=all(checks.values()), volume_ml=ml, win=res["win"],
               dice=ct_volume.dice3d(final, vol["label"]) if vol["label"] is not None else None,
               upsample=res["upsample"], board_ms=res["ms"], pl_ms=res["pl_ms"], upload_s=round(r["upload_s"], 3),
               job_s=round(r["wall_s"], 3), upload_mb=round(res["upload_bytes"] / 1e6, 1),
               stream_s=round(t_stream, 3), pc_post_s=round(t_post, 3), pc_prep_s=round(t_prep, 3),
               old_path_s=round(t_prep + t_stream + t_post, 3), production=prod)
    print("  ct %-10s nz %3d dz %.1f o %d  %s  %.1f mL  job %.2f s (upload %.2f, board %.2f: decode %.2f coarse %.2f "
          "fine %.2f lcc %.2f)  stream %.2f s + PC post %.2f s  PL up %d/%d  %s"
          % (name, nz, dz, o, "PASS" if out["pass_"] else "FAIL " + str([k for k, v in checks.items() if not v]),
             ml, r["wall_s"], r["upload_s"], res["ms"]["total"] / 1000, res["ms"]["decode"] / 1000,
             res["ms"]["coarse"] / 1000, res["ms"]["fine"] / 1000, (res["ms"]["lcc0"] + res["ms"]["lcc1"]) / 1000,
             t_stream, t_post, res["upsample"]["pl"], res["upsample"]["all"], "" if pl else "PS-upsample"),
          flush=True)
    if prod:
        print("      old path (PC prep %.2f + stream %.2f + PC post %.2f) = %.2f s  vs  job (final mask only) %.2f s"
              % (t_prep, t_stream, t_post, t_prep + t_stream + t_post, prod["job_s"]),
          flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--port", type=int, default=5060)
    ap.add_argument("--quick", action="store_true", help="2 卷 CT + 8 对配准")
    ap.add_argument("--only", choices=("reg", "ct"), default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "jobs_acceptance.json"))
    a = ap.parse_args()
    link = imagelink.ImageLink(a.host, a.port, timeout=20.0)
    report = dict(date=time.strftime("%Y-%m-%d %H:%M:%S"), host=a.host)
    report["caps"] = jobs.caps(link)
    print("caps:", report["caps"], flush=True)
    ok = bool(report["caps"].get("ok"))
    if a.only in (None, "reg"):
        print("== F5 registration search on the board", flush=True)
        report["reg"] = check_reg(link, a.quick)
        ok &= report["reg"]["pass_"]
    if a.only in (None, "ct"):
        print("== CT whole volume on the board", flush=True)
        names = VAL[:2] if a.quick else VAL + TRAIN_EXTRA
        report["ct"] = [check_ct(link, n) for n in names]
        report["ct_ps_upsample"] = check_ct(link, names[0], pl=False, compare_stream=False)
        ok &= all(c["pass_"] for c in report["ct"]) and report["ct_ps_upsample"]["pass_"]
        # PS 放大的那次结果也必须和金标准（因而和 PL 那次）一模一样，而且确实没用 PL
        same = {k: report["ct_ps_upsample"]["checks"][k] for k in ("mask_coarse", "mask_final", "volume_ml")}
        report["ct_ps_vs_pl_same"] = all(same.values()) and report["ct_ps_upsample"]["upsample"]["pl"] == 0
        ok &= report["ct_ps_vs_pl_same"]
    report["pass"] = bool(ok)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)
    print("JOBS_BOARD_PASS" if ok else "JOBS_BOARD_FAIL", "->", a.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
