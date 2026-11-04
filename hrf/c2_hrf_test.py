# -*- coding: utf-8 -*-
"""c2_hrf_test.py —— HRF 测试集（Orlando 2017 划分的 30 张）**只评测一次**：在板上用部署的服务端跑（JOB_TILE，层从 /data 读），
各倍数整幅层分块推理 → logit 双线性回 3504x2336 → 门限 -3 → 官方视野内 Dice / 灵敏度 / 特异度 / 细血管召回。

评测哪些倍数、门限、模型都在训练集上定好、写在 tile_config.json 里；这里不选任何东西。
先写锁文件 results/test_lock.json（倍数、门限、代码和训练集结果的 sha256、"used": false），评测完改成 "used": true。
锁文件已是 used 就拒绝再跑。用法：python c2_hrf_test.py --i-am-sure
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
UI = r"E:\PLD\tcp_image\tcp_image_ui"
sys.path.insert(0, UI)
sys.path.insert(0, HERE)
import imagelink              # noqa: E402
import jobs                   # noqa: E402
import tile_engine as te      # noqa: E402
from c1_hrf_mag import load, metrics, to_native, THR   # noqa: E402

LOCK = os.path.join(HERE, "results", "test_lock.json")
OUT = os.path.join(HERE, "results", "c2_hrf_test.json")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def main():
    if "--i-am-sure" not in sys.argv:
        print(__doc__)
        return 2
    if os.path.exists(LOCK) and json.load(open(LOCK, encoding="utf-8")).get("used"):
        print("TEST SET ALREADY USED (%s) - refusing to evaluate again" % LOCK)
        return 3
    sp = json.load(open(os.path.join(HERE, "split.json"), encoding="utf-8"))
    cfg = json.load(open(os.path.join(UI, "tile_config.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(UI, "tile_pyramid.json"), encoding="utf-8"))
    mags = [m for m in cfg["mags"] if m <= float(cfg["cap"])]
    lock = dict(created_utc=datetime.now(timezone.utc).isoformat(), used=False, test=sp["test"], mags=mags,
                thr=THR, model="model 0 (unchanged)", engine="board JOB_TILE, levels from /data/hrf",
                sha256={p: sha(os.path.join(d, p)) for d, p in ((HERE, "c2_hrf_test.py"), (HERE, "c1_hrf_mag.py"),
                                                              (UI, "tile_engine.py"), (UI, "tile_config.json"),
                                                              (HERE, "results/c1_hrf_mag.json"))})
    json.dump(lock, open(LOCK, "w", encoding="utf-8"), indent=1)
    link = imagelink.ImageLink("192.168.1.254", 5060, timeout=60)
    caps = jobs.caps(link)
    by_name = {c["name"]: c for c in man["cases"].values()}
    per = []
    t0 = time.perf_counter()
    for name in sp["test"]:
        rgb, gt, fov = load(name)
        h0, w0 = rgb.shape[:2]
        gt = gt & fov
        thin = gt & ~ndimage.binary_opening(gt, np.ones((5, 5), bool))
        vthin = gt & ~ndimage.binary_opening(gt, np.ones((3, 3), bool))
        row = dict(name=name, runs={})
        for m in mags:
            lv = by_name[name]["levels"]["%.4g" % m]
            out = te.run_board(link, lv["w"], lv["h"], (0, 0, lv["w"], lv["h"]), te.TILE_LOGITS, path=lv["path"], thr=THR)
            r = out["result"]
            if not r.get("ok"):
                raise SystemExit("board failed on %s %gx: %s" % (name, m, r))
            pred = to_native(out["logits"], lv["valid"], (w0, h0)) >= THR
            row["runs"]["%g" % m] = dict(metrics(pred, gt, fov, thin, vthin), tiles=r["tiles"], run=r["run"],
                                        skipped=r["skipped"], job_ms=r["ms"], unet_ms=r["unet_ms"], wall_s=out["wall_s"])
        per.append(row)
        print(name, " ".join("%sx Dice %.4f (%.0f ms)" % (k, v["dice"], v["job_ms"]) for k, v in row["runs"].items()),
              flush=True)
    summary = {}
    for k in per[0]["runs"]:
        rs = [p["runs"][k] for p in per]
        summary[k] = {mm: float(np.mean([r[mm] for r in rs])) for mm in ("dice", "sens", "spec", "thin_recall",
                                                                         "vthin_recall", "job_ms", "tiles", "skipped")}
        summary[k]["by_cat"] = {c: float(np.mean([p["runs"][k]["dice"] for p in per if p["name"].endswith("_" + c)]))
                                for c in ("h", "g", "dr")}
    rep = dict(caps=caps, lock=lock, summary=summary, per_image=per, wall_s=round(time.perf_counter() - t0, 1),
               note="evaluated once; no choice was made on these images")
    json.dump(rep, open(OUT, "w", encoding="utf-8"), indent=1)
    lock.update(used=True, used_utc=datetime.now(timezone.utc).isoformat(), result=OUT, result_sha256=sha(OUT))
    json.dump(lock, open(LOCK, "w", encoding="utf-8"), indent=1)
    link.close()
    for k, v in summary.items():
        print("%sx: Dice %.4f  sens %.4f  spec %.4f  thin<5 %.4f  thin<3 %.4f  (h %.4f g %.4f dr %.4f)  %.0f ms/图"
              % (k, v["dice"], v["sens"], v["spec"], v["thin_recall"], v["vthin_recall"], v["by_cat"]["h"],
                 v["by_cat"]["g"], v["by_cat"]["dr"], v["job_ms"]))
    print("HRF_TEST_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
