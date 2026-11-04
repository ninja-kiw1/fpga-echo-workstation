# -*- coding: utf-8 -*-
"""vessel_morph_check.py —— 板端 src/vessel_morph.c 与独立参考 vessel_morph_ref.py 逐项对照。

1) 已知答案的合成图形：横线、T 形、十字、圆环、正弦曲线、带噪点、空图 ——
   拓扑量（分支 / 端点 / 段数 / 去噪后的血管像素）必须等于预期，迂曲度的大小关系要对；
2) 真实掩膜：20 张 STARE 的模型输出（logit >= -3）、AH 人工标注、128 档、ROI 裁剪，
   C 与 Python 的骨架图逐像素一致，整数指标逐个一致（迂曲度、分形维数允许末位 ±1：
   求和次序与 libm 的 log 末位可能不同）。

用法（需要 numpy / scipy / pillow 与 gcc）：python vessel_morph_check.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

import vessel_morph_ref as ref

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, "..", ".."))
MED = os.path.normpath(os.path.join(RX, "..", "..", "medical_fpga"))
LOGITS = os.path.join(MED, "deployment", "unet_network", "vectors", "images")
STARE = os.path.join(MED, "data", "stare", "png")
FIELDS = ("vessel_px", "fov_px", "length_x10", "caliber_x100", "branches", "endpoints",
          "segments", "tortuosity_x1000", "fractal_x1000", "density_x1000")
LOOSE = ("tortuosity_x1000", "fractal_x1000")

fails = []


def check(cond, what, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", what, detail))
    if not cond:
        fails.append(what)


def build(tmp):
    for gcc in (shutil.which("gcc"), r"C:\msys64\mingw64\bin\gcc.exe"):
        if gcc and os.path.exists(gcc):
            break
    else:
        return None
    exe = os.path.join(tmp, "vessel_morph_dump.exe" if os.name == "nt" else "vessel_morph_dump")
    env = dict(os.environ, PATH=os.path.dirname(gcc) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run([gcc, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I" + os.path.join(RX, "src"), "-o", exe,
                        os.path.join(RX, "test", "vessel_morph_dump.c"),
                        os.path.join(RX, "src", "vessel_morph.c"), "-lm"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(r.stderr)
        return None
    return exe, env


def run_c(tool, tmp, mask, fov, res512):
    exe, env = tool
    h, w = mask.shape
    mp, sp = os.path.join(tmp, "m.bin"), os.path.join(tmp, "s.bin")
    (mask != 0).astype(np.uint8).tofile(mp)
    fp = "-"
    if fov is not None:
        fp = os.path.join(tmp, "f.bin")
        (fov != 0).astype(np.uint8).tofile(fp)
    r = subprocess.run([exe, mp, fp, str(w), str(h), str(res512), sp],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    out = {}
    for line in r.stdout.splitlines():
        k, v = line.split("=")
        out[k] = [int(t) for t in v.split(",")] if "," in v else int(v)
    return out, np.fromfile(sp, dtype=np.uint8).reshape(h, w)


def ref_skel_map(mask, res512):
    """用参考实现的中间结果拼出与板端相同编码的骨架图。"""
    m = np.asarray(mask) != 0
    lab, n = ref.ndimage.label(m, structure=ref.EIGHT)
    area = np.bincount(lab.ravel(), minlength=n + 1)
    min_blob = max(1, (ref.MIN_BLOB_512 * res512 * res512) // (512 * 512))
    keep = area >= min_blob
    keep[0] = False
    b = keep[lab].astype(np.uint8)
    sk = ref.zhang_suen(b)
    p = [ref.shift(sk, dx, dy) for dx, dy in ref.OFFS]
    cn = sum(np.abs(p[k].astype(int) - p[(k + 1) % 8].astype(int)) for k in range(8)) // 2
    code = np.where(b == 1, 1, 0)
    code[sk == 1] = 2
    code[(sk == 1) & (cn >= 3)] = 3
    code[(sk == 1) & (cn == 1)] = 4
    return code.astype(np.uint8)


def compare(tool, tmp, name, mask, fov, res512):
    c, c_skel = run_c(tool, tmp, mask, fov, res512)
    p = ref.analyze(mask, fov, res512)
    bad = [k for k in FIELDS if (abs(c[k] - p[k]) > 1 if k in LOOSE else c[k] != p[k])]
    if c["quad_x1000"] != p["quad_x1000"]:
        bad.append("quad_x1000")
    skel_ok = np.array_equal(c_skel, ref_skel_map(mask, res512))
    check(not bad and skel_ok, name,
          "长度 %.1f  分支 %d  端点 %d  段 %d  迂曲度 %.3f  分形 %.3f%s%s"
          % (c["length_x10"] / 10, c["branches"], c["endpoints"], c["segments"],
             c["tortuosity_x1000"] / 1000, c["fractal_x1000"] / 1000,
             "" if not bad else "  不一致: %s" % bad, "" if skel_ok else "  骨架图不同"))
    return c


def synthetic(tool, tmp):
    print("\n=== 1) 已知答案的合成图形 ===")
    z = lambda: np.zeros((128, 128), np.uint8)
    bar = z(); bar[60:63, 10:110] = 1
    tee = z(); tee[30:33, 10:110] = 1; tee[30:110, 59:62] = 1
    cross = z(); cross[62:65, 10:118] = 1; cross[10:118, 62:65] = 1
    yy, xx = np.mgrid[0:128, 0:128]
    rr = np.hypot(yy - 64, xx - 64)
    ring = ((rr >= 30) & (rr < 33)).astype(np.uint8)
    wave = z()
    for x in range(8, 120):
        y = int(round(64 + 25 * np.sin((x - 8) / 112 * 4 * np.pi)))
        wave[y - 1:y + 2, x] = 1
    wave = ref.ndimage.binary_dilation(wave, iterations=1).astype(np.uint8)
    noisy = bar.copy(); noisy[10:13, 10:13] = 1; noisy[100:102, 100:104] = 1   # 9 px + 8 px 小块
    want = {"横线": (bar, dict(branches=0, endpoints=2, segments=1, tortuosity_x1000=1000)),
            "T 形": (tee, dict(branches=1, endpoints=3, segments=3)),
            "十字": (cross, dict(branches=1, endpoints=4, segments=4)),
            "圆环": (ring, dict(branches=0, endpoints=0, segments=1, tortuosity_x1000=0)),
            "正弦曲线": (wave, dict(branches=0, endpoints=2, segments=1)),
            "带两个小噪点": (noisy, dict(vessel_px=int(bar.sum()), branches=0, endpoints=2)),
            "空图": (z(), dict(vessel_px=0, length_x10=0, branches=0, fractal_x1000=0))}
    res = {}
    for name, (img, exp) in want.items():
        c = compare(tool, tmp, "C == 参考：" + name, img, None, 512)   # 当作全分辨率像素
        res[name] = c
        wrong = {k: (c[k], v) for k, v in exp.items() if c[k] != v}
        check(not wrong, "预期值：" + name, "" if not wrong else "实际/预期 %s" % wrong)
    check(res["正弦曲线"]["tortuosity_x1000"] > 1300,
          "正弦曲线的迂曲度明显大于 1", "%.3f" % (res["正弦曲线"]["tortuosity_x1000"] / 1000))


def real(tool, tmp):
    print("\n=== 2) 真实掩膜：C 与参考逐项一致 ===")
    names = sorted(d for d in os.listdir(LOGITS) if os.path.isdir(os.path.join(LOGITS, d)))
    for n in names:
        lg = np.fromfile(os.path.join(LOGITS, n, "final_actual.bin"), np.int8).reshape(512, 512)
        rgb = np.asarray(Image.open(os.path.join(STARE, "images", n + ".png")).convert("RGB")
                         .resize((512, 512), Image.BILINEAR))
        fov = ref.fov_from_rgb(rgb, 512, 512)
        compare(tool, tmp, "%s 模型 512" % n, lg >= -3, fov, 512)
        if n in ("im0002", "im0324"):
            ah = np.asarray(Image.open(os.path.join(STARE, "labels_ah", n + ".png"))
                            .resize((512, 512), Image.NEAREST)) > 127
            compare(tool, tmp, "%s AH 标注" % n, ah, fov, 512)
            m128 = (lg >= -3)[::4, ::4]
            compare(tool, tmp, "%s 模型 128 档" % n, m128, ref.fov_from_rgb(rgb, 128, 128), 128)
            compare(tool, tmp, "%s ROI(180,150,150x140)" % n, (lg >= -3)[150:290, 180:330],
                    ref.fov_from_rgb(rgb[150:290, 180:330], 150, 140), 512)


def main():
    tmp = tempfile.mkdtemp(prefix="vm_check_")
    try:
        tool = build(tmp)
        if tool is None:
            check(False, "编译 vessel_morph_dump（需要 gcc）")
        else:
            synthetic(tool, tmp)
            real(tool, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 62)
    if fails:
        print("VESSEL_MORPH_CHECK FAILED: %d 项 —— %s" % (len(fails), fails))
        return 1
    print("VESSEL_MORPH_CHECK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
