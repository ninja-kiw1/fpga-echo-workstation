# -*- coding: utf-8 -*-
"""verify_board_tile.py —— 部分 C（服务端 v5.20）大图分块病例任务 JOB_TILE 的实板验收。不换比特流、不改板上配置；
--files 时往板上 /data/hrf/c_verify/ 放几个层文件（结束不删，下次覆盖）。

A  caps：server >= v5.20、任务表里有 "tile"。
B  老 ROI = 1 倍框选的特例：512 帧（golden 图 im0002 / im0324）上 12 个框，
   v5 控制头 ROI 帧的 logit == JOB_TILE（上传 512 层、同一个框）的 logit == 整帧 golden；
   v5 帧回传的叠加 == 用 JOB_TILE 的 logit 在 v5 预处理图上叠加（框内）；pp 掩膜 == logit >= 门限；块就是 proto.roi_crop。
C  大图：STARE / HRF 训练图的若干层（宽 600 / 1200 / 2400，最多 6x4 块）× 若干框（整幅、随机、跨缝）：
   板上每块自有区的 logit == PC 整数逐位参考（tile_ref，与整幅一次推理逐位相同，见 hrf/c1_exactness.py）；
   pp_layer 的血管数 / 4 连通块数 == scipy；掩膜 == logit >= 门限；vis_layer 叠加 == tile_engine.overlay_ref；
   TILE_PS_POST（PS 算计数和叠加）与 PL 结果相同；换计算顺序（倒序 / 按不确定度）结果相同；视野跳过的块里没有视野像素。
D  --files：同样的框从板上 /data 的层文件读（板子只裁剪）== 上传。
E  出错路径：/data 以外的路径、不是排列的顺序 → ok:false，之后正常任务照常。
F  计时：每块（按块大小）、每个视图、裁剪（文件 / 上传）、pp / vis 在 PL 上的时间。

用法：python verify_board_tile.py [--files] [--quick] [--out FILE] → TILE_BOARD_PASS / FAIL
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"E:\PLD\hrf")
import confidence          # noqa: E402
import imagelink           # noqa: E402
import jobs                # noqa: E402
import proto               # noqa: E402
import tile_engine as te   # noqa: E402

HRF = r"E:\PLD\hrf"
CACHE = os.path.join(HRF, "cache")
GOLD = r"E:\PLD\medical_fpga\deployment\unet_network\vectors\images"
STARE = r"E:\PLD\medical_fpga\data\stare\png\images"
THR = -3
BAND = 8
REMOTE = "/data/hrf/c_verify"
ALL = te.TILE_LOGITS | te.TILE_MASK | te.TILE_OVERLAY


class Fail(AssertionError):
    pass


def require(cond, msg):
    if not cond:
        raise Fail(msg)


def golden(name):
    raw = np.fromfile(os.path.join(GOLD, name, "input.bin"), dtype="<i2").reshape(512, 512, 8)
    full = np.fromfile(os.path.join(GOLD, name, "final_actual.bin"), dtype=np.int8).reshape(512, 512)
    return raw[:, :, :3].astype(np.uint8), full


def level_reference(key, lvl):
    """整幅层的参照 logit（分块参照 = 整幅一次推理，c1_exactness 已证）；按层图哈希缓存到 hrf/cache。"""
    os.makedirs(CACHE, exist_ok=True)
    h = hashlib.sha256(lvl.tobytes()).hexdigest()[:16]
    p = os.path.join(CACHE, "%s_%dx%d_%s.npy" % (key.replace("/", "_"), lvl.shape[1], lvl.shape[0], h))
    if os.path.exists(p):
        return np.load(p)
    from tile_ref import Ref
    ref = Ref(limit=True, max_cache=12)
    H, W = lvl.shape[:2]
    R, _ = te.run_tiles(lvl, (0, 0, W, H), ref.run, skip_fov=False)
    ref.close()
    np.save(p, R)
    return R


def check_tiles(out, lvl, R, box, flags, thr, band):
    """一次 JOB_TILE 的结果逐块核对。返回 (比对像素, 统计)。"""
    res = out["result"]
    require(res.get("ok"), "job failed: %s" % res)
    plan = out["plan"]
    fov = te.fov_mask(lvl)
    require(len(out["tiles"]) == len(plan) == res["tiles"], "tile count")
    n = 0
    for t, p in zip(out["tiles"], plan):
        require((t["tx"], t["ty"], t["tw"], t["th"], t["ox"], t["oy"], t["ow"], t["oh"]) ==
                (p.tx, p.ty, p.tw, p.th, p.ox, p.oy, p.ow, p.oh), "plan differs from tile_engine: %s" % p)
        own_fov = fov[t["oy"]:t["oy"] + t["oh"], t["ox"]:t["ox"] + t["ow"]]
        if t["skip"]:
            require(not own_fov.any() and not flags & te.TILE_NO_SKIP, "skipped tile has FOV pixels")
            continue
        require(own_fov.any() or flags & te.TILE_NO_SKIP, "tile without FOV pixels was not skipped")
        ref = R[t["oy"]:t["oy"] + t["oh"], t["ox"]:t["ox"] + t["ow"]]
        m = ref >= thr
        require(t["vessels"] == int(m.sum()), "vessel count (board %d, ref %d)" % (t["vessels"], m.sum()))
        require(t["comps"] == int(ndimage.label(m)[1]), "4-connected components")
        if flags & te.TILE_LOGITS:
            require(np.array_equal(t["logits"], ref), "tile %d logits differ (%d px)"
                    % (t["index"], int((t["logits"] != ref).sum())))
        if flags & te.TILE_MASK:
            require(np.array_equal(t["mask"], m), "tile %d mask bits" % t["index"])
        if flags & te.TILE_OVERLAY:
            base = lvl[t["oy"]:t["oy"] + t["oh"], t["ox"]:t["ox"] + t["ow"]]
            require(np.array_equal(t["overlay"], te.overlay_ref(base, ref, thr, band)), "tile %d overlay" % t["index"])
        n += t["ow"] * t["oh"]
    return n


def tile_stats(out):
    run = [t for t in out["tiles"] if not t["skip"]]
    return dict(tiles=len(out["tiles"]), run=len(run), sizes=[[t["tw"], t["th"]] for t in run],
                tile_run_ms=[round(t["run_us"] / 1000.0, 2) for t in run], tile_crop_ms=[round(t["crop_us"] / 1000.0, 3) for t in run],
                tile_post_ms=[round(t["post_us"] / 1000.0, 3) for t in run], tile_ovl_ms=[round(t["ovl_us"] / 1000.0, 3) for t in run],
                job_ms=out["result"]["ms"], wall_s=round(out["wall_s"], 3), upload_s=round(out["upload_s"], 3),
                **{k: out["result"][k] for k in ("unet_ms", "crop_ms", "post_ms", "ovl_ms", "pp_pl_ms", "vis_pl_ms",
                                                   "halo_overhead", "useful_ratio", "computed_px", "own_px", "box_px",
                                                   "skipped", "post", "overlay", "source")})


def part_b(link, rep, quick):
    """1 倍框选在 512 帧上 = 老 ROI 帧。"""
    rnd = random.Random(20261018)
    boxes = [(180, 150, 150, 140), (0, 0, 64, 64), (470, 500, 42, 12), (201, 183, 127, 95), (0, 0, 512, 512),
             (300, 0, 212, 512)]
    while len(boxes) < (6 if quick else 12):
        w, h = rnd.randrange(8, 400), rnd.randrange(8, 400)
        boxes.append((rnd.randrange(0, 513 - w), rnd.randrange(0, 513 - h), w, h))
    flags = imagelink._parse_flags("proc,overlay,stats,logit")
    items = []
    for name in ("im0002",) if quick else ("im0002", "im0324"):
        rgb, full = golden(name)
        for box in boxes:
            x, y, w, h = box
            roi = None if (w, h) == (512, 512) else box
            imgs, tim, rows, st = link.round_trip(rgb.tobytes(), mode=0, flags=flags,
                                                  aiparam=proto.make_aiparam(0, THR, 512), thr=99, roi=roi)
            plane = np.frombuffer(confidence.logit_plane(imgs["logit"]), np.uint8).astype(np.int16) - 128
            v5 = plane.reshape(512, 512)[y:y + h, x:x + w].astype(np.int8)
            ovl5 = np.frombuffer(imgs["overlay"], np.uint8).reshape(512, 512, 3)[y:y + h, x:x + w]
            out = te.run_board(link, 512, 512, box, ALL | te.TILE_NO_SKIP, level_rgb=rgb, thr=THR, band=0)   # 老 ROI 不跳块
            require(out["result"].get("ok"), "tile job failed: %s" % out["result"])
            require(len(out["plan"]) == 1, "a box on a 512 frame must be one tile")
            t = out["plan"][0]
            if roi is not None:
                require((t.tx, t.ty, t.tw, t.th) == tuple(proto.roi_crop(*box)), "tile != proto.roi_crop")
                require((st["crop_w"], st["crop_h"]) == (t.tw, t.th), "v5 frame crop size != tile size")
            n = check_tiles(out, rgb, full, box, ALL | te.TILE_NO_SKIP, THR, 0)
            require(np.array_equal(v5, full[y:y + h, x:x + w]), "v5 ROI frame != golden")
            require(np.array_equal(out["logits"], v5), "JOB_TILE logits != v5 ROI frame")
            # v5 帧的叠加画在 PL 预处理图上（模式 0 = 灰度），JOB_TILE 画在 RGB 层上：底图不同，
            # 所以比"用 JOB_TILE 的 logit 在 v5 的预处理图上叠加 == v5 回传的叠加"，以及两边涂红的像素完全相同
            proc5 = np.frombuffer(imgs["proc"], np.uint8).reshape(512, 512, 3)[y:y + h, x:x + w]
            require(np.array_equal(te.overlay_ref(proc5, out["logits"], THR, 0), ovl5), "v5 overlay != JOB_TILE logits")
            red = lambda a: (a == (255, 0, 0)).all(axis=2)
            require(np.array_equal(red(out["overlay"]) | (out["logits"] >= THR), red(ovl5) | (out["logits"] >= THR))
                    and np.array_equal(out["logits"] >= THR, v5 >= THR), "painted vessels differ")
            items.append(dict(image=name, box=list(box), crop=[t.tx, t.ty, t.tw, t.th], px=n,
                              v5_ai_ms=st["ai_us"] / 1000.0, tile_unet_ms=out["result"]["unet_ms"],
                              tile_job_ms=out["result"]["ms"]))
            print("  B %s %-22s crop %-20s v5 %.1f ms  tile %.1f ms  == golden == v5" % (
                name, box, (t.tx, t.ty, t.tw, t.th), st["ai_us"] / 1000.0, out["result"]["unet_ms"]), flush=True)
    rep["B_roi_special"] = dict(boxes=len(items), items=items, ok=True)


def levels(quick):
    sp = json.load(open(os.path.join(HRF, "split.json"), encoding="utf-8"))
    out = []
    stare = np.asarray(Image.open(os.path.join(STARE, "im0077.png")).convert("RGB"))
    hrf_name = sp["train"][0]
    hrf = np.asarray(Image.open(os.path.join(HRF, "all", "images", sp["files"][hrf_name]["image"])).convert("RGB"))
    for key, rgb, widths in (("stare_im0077", stare, (600,) if quick else (600, 1200)),
                             ("hrf_" + hrf_name, hrf, (600, 1200) if quick else (600, 1200, 2400))):
        for wd in widths:
            lvl, _ = te.make_level(rgb, wd / float(rgb.shape[1]))
            out.append(("%s_w%d" % (key, wd), lvl))
    return out


def boxes_for(W, H, rnd, n):
    bs = [(0, 0, W, H), (max(0, W // 2 - 300), max(0, H // 2 - 30), min(W, 600), 60),
          (W - min(W, 200), H - min(H, 150), min(W, 200), min(H, 150))]
    for _ in range(n):
        w, h = rnd.randrange(16, min(W, 1100) + 1), rnd.randrange(16, min(H, 800) + 1)
        bs.append((rnd.randrange(0, W - w + 1), rnd.randrange(0, H - h + 1), w, h))
    return bs


def part_c(link, rep, quick, use_files, c):
    rnd = random.Random(7)
    items = []
    views = []
    for key, lvl in levels(quick):
        H, W = lvl.shape[:2]
        t0 = time.perf_counter()
        R = level_reference(key, lvl)
        print("  C %s %dx%d reference %.0f s" % (key, W, H, time.perf_counter() - t0), flush=True)
        path = None
        if use_files:
            path = "%s/%s.lvl" % (REMOTE, key)
            data = te.level_file_bytes(lvl)
            tmp = os.path.join(CACHE, key + ".lvl")
            open(tmp, "wb").write(data)
            sftp = c.open_sftp()
            sftp.put(tmp, path)
            sftp.close()
        for bi, box in enumerate(boxes_for(W, H, rnd, 1 if quick else 3)):
            out = te.run_board(link, W, H, box, ALL, level_rgb=lvl, thr=THR, band=BAND)
            n = check_tiles(out, lvl, R, box, ALL, THR, BAND)
            st = tile_stats(out)
            item = dict(level=key, size=[W, H], box=list(box), px=n, upload=st)
            if bi == 0:                                   # 整幅：再验 PS 路径、换顺序、不跳块
                ps = te.run_board(link, W, H, box, ALL | te.TILE_PS_POST, level_rgb=lvl, thr=THR, band=BAND)
                check_tiles(ps, lvl, R, box, ALL | te.TILE_PS_POST, THR, BAND)
                require(ps["result"]["post"] == "ps" and ps["result"]["overlay"] == "ps", "PS path not taken")
                require(np.array_equal(ps["overlay"], out["overlay"]) and np.array_equal(ps["mask"], out["mask"]),
                        "PS post != PL post")
                order = list(range(len(out["plan"])))[::-1]
                rv = te.run_board(link, W, H, box, te.TILE_LOGITS, level_rgb=lvl, thr=THR, order=order)
                require([t["index"] for t in sorted(rv["tiles"], key=lambda t: t["pos"])] == order, "order ignored")
                require(np.array_equal(rv["logits"], out["logits"]), "reversed order changed the result")
                ns = te.run_board(link, W, H, box, te.TILE_LOGITS | te.TILE_NO_SKIP, level_rgb=lvl, thr=THR)
                check_tiles(ns, lvl, R, box, te.TILE_LOGITS | te.TILE_NO_SKIP, THR, 0)
                item["ps_post"] = tile_stats(ps)
                item["no_skip"] = tile_stats(ns)
            if path is not None:
                fo = te.run_board(link, W, H, box, ALL, path=path, thr=THR, band=BAND)
                check_tiles(fo, lvl, R, box, ALL, THR, BAND)
                require(fo["result"]["source"] == "file", "file source not used")
                require(np.array_equal(fo["overlay"], out["overlay"]) and np.array_equal(fo["logits"], out["logits"]),
                        "file source != upload")
                item["file"] = tile_stats(fo)
            items.append(item)
            views.append(dict(level=key, box=list(box), tiles=st["tiles"], run=st["run"], job_ms=st["job_ms"],
                              unet_ms=st["unet_ms"], file_job_ms=item.get("file", {}).get("job_ms")))
            print("  C %-22s box %-24s %2d 块（跑 %2d）任务 %7.1f ms  U-Net %7.1f ms  halo %.3f  %s" % (
                key, box, st["tiles"], st["run"], st["job_ms"], st["unet_ms"], st["halo_overhead"],
                "文件 %.1f ms" % item["file"]["job_ms"] if "file" in item else ""), flush=True)
    rep["C_large"] = dict(items=items, views=views, ok=True)


def part_e(link, rep):
    r1 = jobs.run(link, te.JOB_TILE, te.TILE_LOGITS,
                  te.tile_request(512, 512, (0, 0, 64, 64), path="/root/x.lvl"), b"")["result"]
    require(r1.get("ok") is False, "path outside /data accepted")
    rgb, full = golden("im0002")
    tiles = te.plan((0, 0, 512, 512), 512, 512)
    r2 = jobs.run(link, te.JOB_TILE, te.TILE_LOGITS,
                  te.tile_request(512, 512, (0, 0, 512, 512), region=(0, 0, 512, 512), order=[len(tiles)] * len(tiles)),
                  rgb.tobytes())["result"]
    require(r2.get("ok") is False, "bad order accepted")
    out = te.run_board(link, 512, 512, (0, 0, 512, 512), te.TILE_LOGITS, level_rgb=rgb)
    require(np.array_equal(out["logits"], full), "normal job after errors")
    rep["E_errors"] = dict(bad_path=r1, bad_order=r2, after_ok=True, ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", action="store_true")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--host", default="192.168.1.254")
    ap.add_argument("--out", default=os.path.join(HERE, "board_check_v5", "tile_board.json"))
    a = ap.parse_args()
    rep = dict(utc=datetime.now(timezone.utc).isoformat(), host=a.host, quick=a.quick, files=a.files, ok=False)
    link = imagelink.ImageLink(a.host, 5060, timeout=30)
    c = None
    try:
        caps = jobs.caps(link)
        rep["A_caps"] = caps
        require("tile" in caps.get("jobs", []) and caps.get("server", "") >= "v5.20", "server without JOB_TILE: %s" % caps)
        print("A caps: server %s, pp %s, vis %s" % (caps["server"], caps["pp"], caps["vis"]), flush=True)
        part_b(link, rep, a.quick)
        if a.files:
            sys.path.insert(0, r"E:\PLD\board_acceptance_v520")
            import b as board
            c = board.connect()
            board.run(c, "mkdir -p %s" % REMOTE, 30)
        part_c(link, rep, a.quick, a.files, c)
        part_e(link, rep)
        rep["ok"] = True
    except Fail as e:
        rep["error"] = str(e)
        print("FAIL:", e)
    finally:
        link.close()
        if c is not None:
            c.close()
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(rep, open(a.out, "w"), indent=1, default=str)
    print("TILE_BOARD_PASS" if rep["ok"] else "TILE_BOARD_FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
