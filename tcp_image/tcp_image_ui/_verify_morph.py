# -*- coding: utf-8 -*-
"""_verify_morph.py —— 血管形态指标（flags b6）的不连板验证：板端打包 ↔ 上位机解包、协议往返。

指标算法本身（板端 C 与独立 NumPy 参考逐项一致、已知答案图形）由
../tcp_image_rx/ai/net/vessel_morph_check.py 验证；与人工标注的一致性见
../tcp_image_rx/docs/vessel_morphology_metrics_eval.md。这里只管“上位机拿到的数是不是板端算的那份”：

1) 板端 vm_fill_block() 打出的 64 字节块，经 proto.unpack_morph() 解出来与 C 打印的指标逐个相同；
   整图网格和 ROI 网格都测；骨架视图 vm_draw() 的输出尺寸正确、框外像素保持底图。
2) 假板端走 imagelink.round_trip：v4 板端回指标块 + 形态视图；v3 老板端不回；AI 没跑出来时不回。
用法：python _verify_morph.py（需要 gcc）
"""

import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import imageio  # noqa: E402
import imagelink  # noqa: E402
import proto  # noqa: E402

RX = os.path.normpath(os.path.join(HERE, "..", "tcp_image_rx"))
LOGITS = os.path.normpath(os.path.join(HERE, "..", "..", "medical_fpga", "deployment",
                                       "unet_network", "vectors", "images"))
failures = []


def check(cond, what, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", what, detail))
    if not cond:
        failures.append(what)


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
    return (exe, env) if r.returncode == 0 else None


def real_mask():
    p = os.path.join(LOGITS, "im0002", "final_actual.bin")
    if os.path.exists(p):
        raw = open(p, "rb").read()
        return bytes(1 if (b - 256 if b >= 128 else b) >= -3 else 0 for b in raw), "im0002 模型掩膜"
    m = bytearray(512 * 512)                     # 没有真实数据时用一个十字
    for i in range(40, 470):
        for d in (-1, 0, 1):
            m[(256 + d) * 512 + i] = m[i * 512 + 256 + d] = 1
    return bytes(m), "合成十字"


def part1(tool, tmp):
    print("\n=== 1) 板端 vm_fill_block 打包 == proto.unpack_morph 解包 ===")
    exe, env = tool
    mask, label = real_mask()
    base, _ = imageio.load_pattern("im0002" if "im0002" in imageio.figure_names() else "bars")
    open(os.path.join(tmp, "base.rgb"), "wb").write(base)
    cases = [("整图 512", mask, 512, 512, 512, (0, 0, 512, 512))]
    x, y, w, h = 180, 150, 150, 140
    roi = bytes(mask[(y + r) * 512 + x + c] for r in range(h) for c in range(w))
    cases.append(("ROI 150x140", roi, w, h, 512, (x, y, w, h)))
    for name, m, gw, gh, res, (rx, ry, rw, rh) in cases:
        mp = os.path.join(tmp, "m.bin")
        open(mp, "wb").write(m)
        args = [exe, mp, "-", str(gw), str(gh), str(res), os.path.join(tmp, "s.bin"),
                os.path.join(tmp, "blk.bin"), os.path.join(tmp, "base.rgb"),
                str(rx), str(ry), str(rw), str(rh), os.path.join(tmp, "view.rgb")]
        r = subprocess.run(args, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            check(False, name, r.stderr)
            continue
        c = {k: v for k, v in (line.split("=") for line in r.stdout.splitlines())}
        d = proto.unpack_morph(open(os.path.join(tmp, "blk.bin"), "rb").read())
        same = all(int(c[k]) == d[k] for k in ("vessel_px", "fov_px", "length_x10", "caliber_x100",
                                              "branches", "endpoints", "segments",
                                              "tortuosity_x1000", "fractal_x1000", "density_x1000"))
        quads = [int(v) / 10.0 for v in c["quad_x1000"].split(",")]
        check(same and quads == d["quad_pct"] and (d["w"], d["h"]) == (gw, gh),
              "%s（%s）" % (name, label), "总长 %.1f  分支 %d  分形 %.3f" % (d["length"], d["branches"], d["fractal"]))
        view = open(os.path.join(tmp, "view.rgb"), "rb").read()
        outside_ok = all(view[(yy * 512 + xx) * 3:(yy * 512 + xx) * 3 + 3]
                         == base[(yy * 512 + xx) * 3:(yy * 512 + xx) * 3 + 3]
                         for yy, xx in ((5, 5), (500, 500), (ry - 3, rx - 3)) if yy >= 0 and xx >= 0
                         and not (rx <= xx < rx + rw and ry <= yy < ry + rh))
        check(len(view) == proto.IMG_TOTAL_BYTES and outside_ok and view != base,
              "%s 骨架视图（区域外保持底图）" % name)


class FakeBoard(threading.Thread):
    def __init__(self, version, ai_us):
        super().__init__(daemon=True)
        self.version, self.ai_us = version, ai_us
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]

    def run(self):
        c, _ = self.srv.accept()
        buf = b""
        while len(buf) < proto.WIRE_TOTAL_BYTES_V4:
            buf += c.recv(proto.WIRE_TOTAL_BYTES_V4 - len(buf))
        flags = struct.unpack_from("<H", buf, 4)[0]
        ai_ok = self.ai_us != 0
        morph = bool(flags & proto.FLAG_MORPH) and self.version >= 4 and ai_ok \
            and bool(flags & proto.FLAG_STATS)
        out = struct.pack("<16I", proto.AI_STAT_MAGIC, self.version, flags, 512, 1, 2, 0, 0, 0,
                          self.ai_us, 0, 0, 255, 0, 1, 0)
        if morph:
            out += struct.pack("<16I", proto.MORPH_MAGIC, 1, 512 | (512 << 16), 10, 20, 1234,
                               350, 5, 6, 7, 1160, 1440, 105, 1 | (2 << 16), 3 | (4 << 16), 12000)
        n_img = (1 if flags & proto.FLAG_ECHO_PROC else 0) \
            + (1 if flags & proto.FLAG_ECHO_OVER and ai_ok else 0) + (1 if morph else 0)
        out += b"".join(proto.iter_packets(bytes(proto.IMG_TOTAL_BYTES))) * n_img
        c.sendall(out)
        c.close()


def part2():
    print("\n=== 2) 协议：假板端走 imagelink.round_trip ===")
    flags = proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER | proto.FLAG_STATS | proto.FLAG_MORPH
    for label, ver, ai_us, want_imgs, want_morph in (
            ("新板端 v4", 4, 600000, ["proc", "overlay", "morph"], True),
            ("老板端 v3（不认 b6）", 3, 600000, ["proc", "overlay"], False),
            ("AI 没跑出来", 4, 0, ["proc"], False)):
        fb = FakeBoard(ver, ai_us)
        fb.start()
        link = imagelink.ImageLink("127.0.0.1", fb.port, timeout=5.0)
        try:
            imgs, _t, _r, st = link.round_trip(bytes(proto.IMG_TOTAL_BYTES), 0, flags=flags)
            got = [k for k in ("proc", "overlay", "mask", "logit", "morph") if k in imgs]
            m = st.get("morph")
            ok = got == want_imgs and (m is not None) == want_morph
            if want_morph:
                ok = ok and m["length"] == 123.4 and m["quad_pct"] == [0.1, 0.2, 0.3, 0.4]
            check(ok, label, "图 %s，指标块 %s" % (got, "有" if m else "无"))
        except Exception as e:                   # noqa: BLE001
            check(False, label, "异常：%s" % e)
        finally:
            link.close()


def main():
    if proto.SELF_CHECK_ERRORS:
        check(False, "proto self-check", str(proto.SELF_CHECK_ERRORS))
    tmp = tempfile.mkdtemp(prefix="morph_verify_")
    try:
        tool = build(tmp)
        if tool is None:
            check(False, "编译 vessel_morph_dump（需要 gcc）")
        else:
            part1(tool, tmp)
        part2()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 62)
    if failures:
        print("MORPH_VERIFY FAILED: %d 项 —— %s" % (len(failures), failures))
        return 1
    print("MORPH_VERIFY OK: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
