# -*- coding: utf-8 -*-
"""c2_pyramid.py —— 部分 C：在 PC 上做图像金字塔，放到板上 /data/hrf，板子推理时只裁剪（pread）。

  build  每张图、每个放大倍数（tile_config.json 的 mags 里 <= cap 的）：tile_engine.make_level（LANCZOS + 补黑到 8 的倍数）
         → pyramid/<图>/m<倍数>.lvl（32 字节头 "PLDL" + RGB）+ pyramid/manifest.json（键 = 原图 RGB 的 sha256）
  push   SFTP 传到板上 /data/hrf/<图>/，板上 sha256sum 逐个核对；manifest 复制到 tcp_image_ui/tile_pyramid.json
  status 板上 /data/hrf 占用

图：HRF 45 张（CC BY 4.0）+ 上位机 samples 里的 5 张 STARE。每张图只做到 tile_engine.mag_cap（模型上限与原图分辨率取小）。放上板只为浏览演示；HRF 测试集的 Dice 只按锁文件评测一次。
用法：python c2_pyramid.py build|push|status
"""
import hashlib
import json
import os
import shutil
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
UI = r"E:\PLD\tcp_image\tcp_image_ui"
sys.path.insert(0, UI)
sys.path.insert(0, r"E:\PLD\board_acceptance_v520")
import tile_engine as te    # noqa: E402

OUT = os.path.join(HERE, "pyramid")
REMOTE = "/data/hrf"


def sources():
    sp = json.load(open(os.path.join(HERE, "split.json"), encoding="utf-8"))
    for name in sp["train"] + sp["test"]:
        yield name, os.path.join(HERE, "all", "images", sp["files"][name]["image"]), \
            "train" if name in sp["train"] else "test"
    for f in sorted(os.listdir(os.path.join(UI, "samples"))):
        yield "stare_" + os.path.splitext(f)[0], os.path.join(UI, "samples", f), "sample"


def build():
    cfg = json.load(open(os.path.join(UI, "tile_config.json"), encoding="utf-8"))
    mags = [m for m in cfg["mags"] if m <= float(cfg["cap"])]
    os.makedirs(OUT, exist_ok=True)
    man = dict(note="levels = tile_engine.make_level(original, scale_for_mag(m, width0)); key = sha256 of the original RGB",
               w1x=te.W1X, mags=mags, cases={})
    total = 0
    for name, path, part in sources():
        rgb = np.asarray(Image.open(path).convert("RGB"))
        key = hashlib.sha256(np.ascontiguousarray(rgb).tobytes()).hexdigest()
        h0, w0 = rgb.shape[:2]
        d = os.path.join(OUT, name)
        os.makedirs(d, exist_ok=True)
        cap = te.mag_cap(w0, cfg["cap"])           # 层不比原图大（再大是插值，没有新像素）
        mags_img = [m for m in mags if m <= cap + 1e-9]
        if cap < float(cfg["cap"]) - 1e-9 and all(abs(m - cap) > 1e-9 for m in mags_img):
            mags_img.append(cap)                  # 原图分辨率那一层
        case = dict(name=name, part=part, source=os.path.basename(path), w0=w0, h0=h0, cap=cap, levels={})
        for m in mags_img:
            s = te.scale_for_mag(m, w0)
            lvl, valid = te.make_level(rgb, s)
            data = te.level_file_bytes(lvl)
            fn = "m%.4g.lvl" % m
            open(os.path.join(d, fn), "wb").write(data)
            total += len(data)
            case["levels"]["%.4g" % m] = dict(path="%s/%s/%s" % (REMOTE, name, fn), w=lvl.shape[1], h=lvl.shape[0],
                                           valid=list(valid), scale=s, bytes=len(data),
                                           sha256=hashlib.sha256(data).hexdigest())
        man["cases"][key] = case
        print("%-14s %s %dx%d -> %s" % (name, part, w0, h0, ", ".join("%.4gx:%dx%d" % (m, case["levels"]["%.4g" % m]["w"],
                                                                                          case["levels"]["%.4g" % m]["h"])
                                                                         for m in mags_img)), flush=True)
    man["total_bytes"] = total
    json.dump(man, open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("%d cases, %.1f MB" % (len(man["cases"]), total / 1e6))


def push():
    import b as board
    man = json.load(open(os.path.join(OUT, "manifest.json"), encoding="utf-8"))
    c = board.connect()
    try:
        sftp = c.open_sftp()
        lines = []
        for key, case in man["cases"].items():
            board.run(c, "mkdir -p %s/%s" % (REMOTE, case["name"]), 30)
            for m, lv in case["levels"].items():
                sftp.put(os.path.join(OUT, case["name"], os.path.basename(lv["path"])), lv["path"])
                lines.append("%s  %s" % (lv["sha256"], lv["path"]))
            print("pushed", case["name"], flush=True)
        sftp.putfo(__import__("io").BytesIO(("\n".join(lines) + "\n").encode()), REMOTE + "/levels.sha256")
        rc = board.run(c, "cd / && sha256sum -c --quiet %s/levels.sha256 && echo PYRAMID_SHA_OK; du -sh %s; df -h /data | tail -1"
                       % (REMOTE, REMOTE), 600)
        if rc != 0:
            raise SystemExit("sha256 check on the board failed")
        sftp.close()
    finally:
        c.close()
    shutil.copy2(os.path.join(OUT, "manifest.json"), os.path.join(UI, "tile_pyramid.json"))
    print("manifest -> tcp_image_ui/tile_pyramid.json")


def status():
    import b as board
    c = board.connect()
    try:
        board.run(c, "du -sh %s; ls %s | head; df -h /data | tail -1" % (REMOTE, REMOTE), 60)
    finally:
        c.close()


if __name__ == "__main__":
    dict(build=build, push=push, status=status)[sys.argv[1]]()
