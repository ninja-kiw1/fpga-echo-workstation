# -*- coding: utf-8 -*-
"""Board acceptance: PL post-processing (pp_layer, FLAG_C_PP) through the server (v5.10+, firmware xu2dp_e2b_pp).

For every frame the board returns its int8 logits (b8) AND the pp_layer result (b10); the pp result must equal the
host code that the UI used so far, computed from those same board logits:
  A  EchoNet (model 16, VAL videos): keep-largest + fill-holes in the 112 crop == echo_ws.clean(mask_from_logit);
     components == scipy labels of the raw mask (the QC "ncomp")
  B  CT (model 14 coarse, x4) == ct_volume.upsample_bilinear(q, 4) > 0.5; stage 2 (model 15 in the ROI window, x2)
     == upsample_bilinear(q, 2) > 0.5; cases from train / val (never the test split)
  C  fundus 512 (model 0, thr -3, sent in two row bands by the driver): mask == logits >= -3, count / components ==
     4-connected labels (= the board's ai_view_count)
  D  a bad window gets status 4 and the next frame is fine; reserved bit2 says pp is available
Also reports pl_us and the round trip with and without b10.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe verify_board_pp.py [--videos 10]
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import ct_volume                # noqa: E402
import echo_ws                  # noqa: E402
import imagelink                # noqa: E402
import proto                    # noqa: E402

OUT = os.path.join(HERE, "board_check_v5", "pp_acceptance.json")
F = proto.FLAG_STATS | proto.FLAG_C_LOGIT


def trip(link, payload, w, kind, model, thr, pp):
    t0 = time.perf_counter()
    link.send_v6c(payload, w, kind=kind, model0=model, thr=thr, flags=F, pp=pp)
    st, lg, _ = link.recv_v6c(F | (proto.FLAG_C_PP if pp else 0), w)
    return st, np.frombuffer(lg, np.int8).reshape(w, w), (time.perf_counter() - t0) * 1000.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--videos", type=int, default=10)
    ap.add_argument("--ct", nargs="+", default=["spleen_2", "spleen_10"])
    a = ap.parse_args()
    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=60)
    rep = dict(utc=datetime.now(timezone.utc).isoformat(), checks={})
    ok_all = True

    def check(name, cond, **info):
        nonlocal ok_all
        rep["checks"][name] = dict(passed=bool(cond), **info)
        ok_all &= bool(cond)
        print("%s  %s %s" % ("PASS" if cond else "FAIL", name, json.dumps(info, ensure_ascii=False)[:300]), flush=True)

    # ---- D (first): capability bit, bad window, stream still in sync ----
    z = bytes(128 * 128 * 3)
    st, _, _ = trip(link, z, 128, proto.V6C_KIND_RGB, 16, 1, None)
    avail = proto.stats_pp_available(st) and link.pp_available
    st_bad, _, _ = trip(link, z, 128, proto.V6C_KIND_RGB, 16, 1, dict(op=proto.PP_OP_MASK, win=(100, 100, 64, 64)))
    st_ok, _, _ = trip(link, z, 128, proto.V6C_KIND_RGB, 16, 1, dict(op=proto.PP_OP_MASK))
    check("D_capability_and_bad_window", avail and st["version"] >= 8 and st_bad["pp"]["status"] == 4 and
          st_bad["pp"]["mask"] is None and st_ok["pp"]["status"] == 0 and st_ok["pp"]["rows"] == 128,
          version=st["version"], bad_status=st_bad["pp"]["status"])
    if not avail:
        print("PP_BOARD_UI_FAIL (board does not offer pp)")
        return 1

    # ---- A EchoNet ----
    vids = [r["FileName"] for r in csv.DictReader(open(os.path.join(PLD, "EchoNet-Dynamic", "FileList.csv")))
            if r["Split"] == "VAL"][:a.videos]
    nfr, bad, pl, rt_pp = 0, [], [], []
    for vid in vids:
        frames, fps = echo_ws.read_avi(os.path.join(PLD, "EchoNet-Dynamic", "Videos", vid + ".avi"))
        _, pls = echo_ws.payloads(frames, fps, "t3", 1)
        for j, p in enumerate(pls):
            st, lg, ms = trip(link, p, 128, proto.V6C_KIND_RGB, 16, 1, echo_ws.pp_request())
            q, raw = echo_ws.mask_from_logit(lg.tobytes())
            want_mask, want_n = echo_ws.clean(raw) if raw.any() else (raw, 0)
            got = echo_ws.pp_clean(st["pp"])
            if got is None or not np.array_equal(got[0], want_mask) or got[1] != want_n or \
                    st["pp"]["count"] != int(raw.sum()):
                bad.append("%s:%d" % (vid, j))
            pl.append(st["pp"]["pl_us"])
            rt_pp.append(ms)
            nfr += 1
    check("A_echonet_clean", not bad and nfr > 0, frames=nfr, videos=len(vids), mismatches=bad[:10],
          pl_ms=round(float(np.mean(pl)) / 1000, 3), rt_ms=round(float(np.mean(rt_pp)), 2))

    # ---- B CT coarse x4 + stage 2 x2 ----
    for case in a.ct:
        v = ct_volume.load_ct(os.path.join(PLD, "Task09_Spleen", "Task09_Spleen", "imagesTr", case + ".nii.gz"))
        u8 = ct_volume.window_u8(v["hu"])
        pls = ct_volume.payloads(u8, v["spacing"][2])
        logits, masks, bad, pl = [], [], [], []
        for zi, p in enumerate(pls):
            st, lg, _ = trip(link, p, 128, proto.V6C_KIND_RGB, 14, 1, ct_volume.pp_request(4))
            m = ct_volume.pp_mask(st["pp"], 512)
            want = ct_volume.upsample_bilinear(lg[None], 4)[0] > 0.5
            if m is None or not np.array_equal(m, want):
                bad.append(zi)
            logits.append(lg)
            masks.append(want if m is None else m)
            pl.append(st["pp"]["pl_us"])
        logits = np.stack(logits)
        same_vol = np.array_equal(ct_volume.postprocess_masks(np.stack(masks), "bilinear+lcc"),
                                  ct_volume.postprocess(logits, "bilinear+lcc"))
        check("B_ct_%s_coarse_x4" % case, not bad and same_vol, slices=len(pls), mismatches=bad[:10],
              volume_mask_same=same_vol, pl_ms=round(float(np.mean(pl)) / 1000, 3))
        m0 = ct_volume.postprocess(logits, "bilinear+lcc")
        win = ct_volume.roi_window(m0, v["spacing"][2])
        if win is None:
            check("B_ct_%s_roi_x2" % case, False, reason="empty coarse mask")
            continue
        fpl = ct_volume.roi_payloads(u8, m0, win, "roi_prior", v["spacing"][2])
        fl, fm, bad, pl = [], [], [], []
        for i, p in enumerate(fpl):
            st, lg, _ = trip(link, p, 128, proto.V6C_KIND_RGB, 15, 1, ct_volume.pp_request(2))
            m = ct_volume.pp_mask(st["pp"], 256)
            want = ct_volume.upsample_bilinear(lg[None], 2)[0] > 0.5
            if m is None or not np.array_equal(m, want):
                bad.append(i)
            fl.append(lg)
            fm.append(want if m is None else m)
            pl.append(st["pp"]["pl_us"])
        same_vol = np.array_equal(ct_volume.roi_combine_masks(np.stack(fm), win, len(pls)),
                                  ct_volume.roi_combine(np.stack(fl), win, len(pls)))
        check("B_ct_%s_roi_x2" % case, not bad and same_vol, slices=len(fpl), mismatches=bad[:10],
              volume_mask_same=same_vol, pl_ms=round(float(np.mean(pl)) / 1000, 3))

    # ---- C fundus 512 (two row bands inside the driver) ----
    import imageio as uiio
    bad, pl, rt_pp, rt_no = [], [], [], []
    for i in range(1, 6):
        raw, _ = uiio.load_image(os.path.join(HERE, "samples", "im%04d.ppm" % i))
        st, lg, ms = trip(link, raw, 512, proto.V6C_KIND_RGB, 0, -3, dict(op=proto.PP_OP_MASK, thr=-3))
        _, _, ms0 = trip(link, raw, 512, proto.V6C_KIND_RGB, 0, -3, None)
        m = lg >= -3
        lab, n = ndimage.label(m)
        got = proto.pp_mask_array(st["pp"])[:512, :512] if st["pp"]["mask"] else None
        if got is None or not np.array_equal(got, m) or st["pp"]["count"] != int(m.sum()) or st["pp"]["comps"] != n:
            bad.append(i)
        pl.append(st["pp"]["pl_us"])
        rt_pp.append(ms)
        rt_no.append(ms0)
    check("C_fundus512_mask_count", not bad, mismatches=bad, pl_ms=round(float(np.mean(pl)) / 1000, 3),
          rt_ms_with_pp=round(float(np.mean(rt_pp)), 1), rt_ms_without=round(float(np.mean(rt_no)), 1))
    link.close()

    rep["pass"] = ok_all
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2, ensure_ascii=False)
    print("PP_BOARD_UI_PASS" if ok_all else "PP_BOARD_UI_FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
