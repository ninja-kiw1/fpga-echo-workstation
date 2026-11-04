# -*- coding: utf-8 -*-
"""_verify_confidence.py —— 置信度热力图（flags b5 = logit 图）的不连板验证。

1) 逐字节：用板端**原样的** ai_view.c（经 ../tcp_image_rx/test/ai_view_dump.c 编成主机程序）
   生成掩膜 / 叠加 / logit 三幅回传图，再用 confidence.py 从 logit 图本地重算掩膜与叠加，
   要求逐字节一致；同时核对 logit 图解码、血管计数（网络分辨率口径）。
   数据：medical_fpga 整网 C 仿真的真实 logit（有就用）+ 覆盖 -128..127 的合成 logit，
   分辨率 512 / 256 / 32，门限 -128 / -20 / -3 / 0 / 15 / 127。
2) 协议：本机起一个假板端，走 imagelink.round_trip 的完整收发，覆盖
   新板端（统计块 v2，发 logit）/ 老板端（v1，不发 logit）/ 这帧 AI 没跑出来（ai_us=0）。
3) 计时：一次本地重算（掩膜 + 叠加 + 热力图 + 不确定区域 + 统计）的耗时。

需要 gcc（MinGW 或 Linux）。用法：python _verify_confidence.py
"""

import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import confidence  # noqa: E402
import imageio  # noqa: E402
import imagelink  # noqa: E402
import proto  # noqa: E402

RX = os.path.normpath(os.path.join(HERE, "..", "tcp_image_rx"))
REAL_LOGITS = os.path.normpath(os.path.join(
    HERE, "..", "..", "medical_fpga", "deployment", "unet_network", "vectors", "images"))

failures = []


def check(cond, what, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", what, detail))
    if not cond:
        failures.append(what)


def find_gcc():
    for cand in (shutil.which("gcc"), r"C:\msys64\mingw64\bin\gcc.exe"):
        if cand and os.path.exists(cand):
            return cand
    return None


def build_harness(tmp):
    gcc = find_gcc()
    if gcc is None:
        return None
    exe = os.path.join(tmp, "ai_view_dump.exe" if os.name == "nt" else "ai_view_dump")
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(gcc) + os.pathsep + env.get("PATH", "")
    cmd = [gcc, "-std=c99", "-O2", "-Wall", "-Wextra", "-Werror",
           "-I" + os.path.join(RX, "src"), "-o", exe,
           os.path.join(RX, "test", "ai_view_dump.c"), os.path.join(RX, "src", "ai_view.c")]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        return None
    return exe, env


def upsample_plane(logits, lw, lh):
    """与板端相同的最近邻映射，直接得到 512x512 的偏移码平面（作独立参照）。"""
    out = bytearray(confidence.N_PIX)
    for y in range(proto.IMG_HEIGHT):
        ly = (y * lh) // proto.IMG_HEIGHT
        row = logits[ly * lw:(ly + 1) * lw]
        for x in range(proto.IMG_WIDTH):
            out[y * proto.IMG_WIDTH + x] = (row[(x * lw) // proto.IMG_WIDTH] + 128) & 0xFF
    return bytes(out)


def cases():
    rnd = random.Random(20261007)
    synth = [((i * 37 + rnd.randrange(256)) % 256) - 128 for i in range(512 * 512)]
    synth[:256] = list(range(-128, 128))            # 保证整个 int8 范围都出现
    yield "synthetic", synth, 512
    real = []
    for name in ("im0002", "im0324"):
        p = os.path.join(REAL_LOGITS, name, "final_actual.bin")
        if os.path.exists(p):
            raw = open(p, "rb").read()
            real.append((name, [b - 256 if b >= 128 else b for b in raw]))
    if not real:
        print("  (未找到 medical_fpga 的真实 logit，只用合成数据)")
    for name, lg in real:
        yield name, lg, 512
    src = real[0][1] if real else synth
    for res in (256, 32):                         # 低分辨率档：从 512 图上抽点得到 res x res
        f = 512 // res
        yield "res%d" % res, [src[(y * f) * 512 + x * f] for y in range(res) for x in range(res)], res


def part1_bytes(exe, env, tmp):
    print("\n=== 1) 本地重算 vs 板端 ai_view.c，逐字节 ===")
    base, _ = imageio.load_pattern("bars")
    base_path = os.path.join(tmp, "base.rgb")
    open(base_path, "wb").write(base)
    for name, logits, res in cases():
        lpath = os.path.join(tmp, "logits.bin")
        open(lpath, "wb").write(bytes(v & 0xFF for v in logits))
        ref_plane = upsample_plane(logits, res, res)
        for thr in (-128, -20, -3, 0, 15, 127):
            r = subprocess.run([exe, lpath, str(res), str(res), base_path, str(thr), tmp],
                               capture_output=True, text=True, env=env)
            if r.returncode != 0:
                check(False, "%s thr=%d harness run" % (name, thr), r.stderr)
                continue
            c = dict(kv.split("=") for kv in r.stdout.split())
            c_mask = open(os.path.join(tmp, "mask.rgb"), "rb").read()
            c_over = open(os.path.join(tmp, "overlay.rgb"), "rb").read()
            c_logit = open(os.path.join(tmp, "logit.rgb"), "rb").read()
            plane = confidence.logit_plane(c_logit)
            st = confidence.stats(plane, thr, 8, res)
            ok = (plane == ref_plane
                  and confidence.mask_rgb(plane, thr) == c_mask
                  and confidence.overlay_rgb(base, plane, thr) == c_over
                  and st["vessels"] == int(c["vessels"]) and st["total"] == int(c["total"]))
            check(ok, "%-9s %3dx%-3d thr=%4d" % (name, res, res, thr),
                  "vessels=%s/%s" % (c["vessels"], c["total"]))


class FakeBoard(threading.Thread):
    """收一帧（v4 头 + 图像流），按 flags 回：统计块 + 各图像流。"""

    def __init__(self, version, ai_us):
        super().__init__(daemon=True)
        self.version, self.ai_us = version, ai_us
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]

    def run(self):
        c, _ = self.srv.accept()
        need = proto.WIRE_TOTAL_BYTES_V4
        buf = b""
        while len(buf) < need:
            chunk = c.recv(need - len(buf))
            if not chunk:
                return
            buf += chunk
        _magic, _mode, flags, _aip, _thr, _r = struct.unpack_from("<HHHHHH", buf, 0)
        ai_ok = self.ai_us != 0
        out = b""
        if flags & proto.FLAG_STATS:
            out += struct.pack("<16I", proto.AI_STAT_MAGIC, self.version, flags, 512, 1, 2,
                               0, 0, 0, self.ai_us, 0, 0, 0, 0, 1, 0)
        img = bytes(range(256)) * (proto.IMG_TOTAL_BYTES // 256)
        sent = []
        if flags & proto.FLAG_ECHO_PROC:
            sent.append("proc")
        if flags & proto.FLAG_ECHO_OVER and ai_ok:
            sent.append("overlay")
        if flags & proto.FLAG_ECHO_MASK and ai_ok:
            sent.append("mask")
        # 只有 v2 板端认 b5；老板端会忽略这一位
        if flags & proto.FLAG_ECHO_LOGIT and ai_ok and self.version >= 2:
            sent.append("logit")
        for _ in sent:
            out += b"".join(proto.iter_packets(img))
        c.sendall(out)
        time.sleep(0.2)
        c.close()


def part2_protocol():
    print("\n=== 2) 协议：假板端走 imagelink.round_trip 完整收发 ===")
    flags = (proto.FLAG_ECHO_PROC | proto.FLAG_ECHO_OVER | proto.FLAG_ECHO_MASK
             | proto.FLAG_ECHO_LOGIT | proto.FLAG_STATS)
    image = bytes(proto.IMG_TOTAL_BYTES)
    for label, version, ai_us, want in (
            ("新板端 v2", 2, 600000, ["proc", "overlay", "mask", "logit"]),
            ("老板端 v1", 1, 600000, ["proc", "overlay", "mask"]),
            ("AI 没跑出来", 2, 0, ["proc"])):
        fb = FakeBoard(version, ai_us)
        fb.start()
        link = imagelink.ImageLink("127.0.0.1", fb.port, timeout=5.0)
        try:
            images, _tim, rows, st = link.round_trip(image, 0, flags=flags)
            got = [k for k in ("proc", "overlay", "mask", "logit") if k in images]
            crc_ok = all(r["crc_errors"] == 0 for r in rows.values())
            check(got == want and crc_ok, label, "收到 %s" % got)
        except Exception as e:                   # noqa: BLE001 - 测试里报出来即可
            check(False, label, "异常：%s" % e)
        finally:
            link.close()


def part3_timing():
    print("\n=== 3) 本地重算耗时（512x512） ===")
    rnd = random.Random(1)
    plane = bytes(rnd.randrange(256) for _ in range(confidence.N_PIX))
    base, _ = imageio.load_pattern("bars")
    t0 = time.perf_counter()
    for thr in (-3, 10, -10):
        confidence.mask_rgb(plane, thr)
        confidence.overlay_rgb(base, plane, thr)
        confidence.uncertain_rgb(base, plane, thr, 8)
        confidence.stats(plane, thr, 8)
    confidence.heat_rgb(plane)
    ms = (time.perf_counter() - t0) * 1000.0 / 3
    check(ms < 300, "一次换门限的全部重算", "%.1f ms" % ms)


def main():
    if proto.SELF_CHECK_ERRORS:
        check(False, "proto self-check", str(proto.SELF_CHECK_ERRORS))
    tmp = tempfile.mkdtemp(prefix="conf_verify_")
    try:
        built = build_harness(tmp)
        if built is None:
            check(False, "编译 ai_view_dump（需要 gcc）")
        else:
            part1_bytes(built[0], built[1], tmp)
        part2_protocol()
        part3_timing()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 62)
    if failures:
        print("CONFIDENCE_VERIFY FAILED: %d 项 —— %s" % (len(failures), failures))
        return 1
    print("CONFIDENCE_VERIFY OK: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
