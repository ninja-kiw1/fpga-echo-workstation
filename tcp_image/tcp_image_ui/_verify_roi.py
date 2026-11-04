# -*- coding: utf-8 -*-
"""_verify_roi.py —— ROI 局部推理（控制头 v5）的不连板验证。

1) 裁剪窗口：板端 ai_view_roi_crop()（经 ../tcp_image_rx/test/ai_view_dump.c 编成主机程序）
   与上位机 proto.roi_crop()、精确性证明脚本 roi_exactness.crop_for_roi() 三份逐个比对，
   覆盖随机框和贴边 / 整图 / 1 像素等边界情况；并检查对齐与边距规则。
2) 贴回：板端 ai_view_roi_paste() 与 Python 参照逐字节一致（ROI 外 = -128）。
3) 协议：假板端解析 v5 头，ROI 字段原样到达；统计块 v3 的裁剪尺寸能解出来。
4) confidence.roi_compare()：ROI 结果与整图 logit 的比对逻辑。

ROI 内结果与整图逐值一致这件事本身，由 ../tcp_image_rx/ai/net/roi_exactness.py 证明
（NumPy 逐位参考，36 组 ROI 全部一致）。用法：python _verify_roi.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import confidence  # noqa: E402
import imagelink  # noqa: E402
import proto  # noqa: E402
from _verify_confidence import build_harness  # noqa: E402

failures = []


def check(cond, what, detail=""):
    print("  [%s] %s %s" % ("PASS" if cond else "FAIL", what, detail))
    if not cond:
        failures.append(what)


def rois():
    rnd = random.Random(20261007)
    edge = [(0, 0, 1, 1), (511, 511, 1, 1), (0, 0, 512, 512), (500, 500, 12, 12),
            (0, 200, 40, 40), (480, 10, 32, 300), (5, 5, 3, 3), (488, 0, 24, 512),
            (200, 180, 128, 128), (400, 420, 112, 92), (2, 2, 508, 508)]
    out = list(edge)
    for _ in range(2000):
        w, h = rnd.randint(1, 512), rnd.randint(1, 512)
        out.append((rnd.randint(0, 512 - w), rnd.randint(0, 512 - h), w, h))
    return out


def part1_crop(exe, env):
    print("\n=== 1) 裁剪窗口：板端 C / proto.roi_crop / roi_exactness 三份一致 ===")
    rs = rois()
    r = subprocess.run([exe, "--roi"], input="\n".join("%d %d %d %d" % t for t in rs),
                       capture_output=True, text=True, env=env)
    c_out = [tuple(int(v) for v in line.split()) for line in r.stdout.splitlines()]
    py_out = [proto.roi_crop(*t) for t in rs]
    check(len(c_out) == len(rs) and c_out == py_out, "板端 C == proto.roi_crop",
          "%d 个框" % len(rs))
    bad = 0
    for (x, y, w, h), (cx, cy, cw, ch) in zip(rs, py_out):
        if (cx % 4 or cy % 4 or cw % 8 or ch % 8 or cx < 0 or cy < 0
                or cx + cw > 512 or cy + ch > 512
                or cx > max(0, x - proto.ROI_MARGIN) or cy > max(0, y - proto.ROI_MARGIN)
                or cx + cw < min(512, x + w + proto.ROI_MARGIN)
                or cy + ch < min(512, y + h + proto.ROI_MARGIN)):
            bad += 1
    check(bad == 0, "对齐与边距规则（起点 4 对齐、尺寸 8 的倍数、每侧留够 24 或到图边）",
          "违规 %d" % bad)
    try:
        sys.path.insert(0, os.path.join(HERE, "..", "tcp_image_rx", "ai", "net"))
        import roi_exactness  # noqa: E402  需要 numpy 和 ../../medical_fpga
        same = all(roi_exactness.crop_for_roi(*t) == p for t, p in zip(rs, py_out))
        check(same, "proto.roi_crop == roi_exactness.crop_for_roi（证明里用的那份）")
    except Exception as e:                       # noqa: BLE001 - 环境里可能没有
        print("  (跳过 roi_exactness 对照：%s)" % e)


def part2_paste(exe, env, tmp):
    print("\n=== 2) 贴回：板端 ai_view_roi_paste vs Python 参照 ===")
    rnd = random.Random(7)
    for (x, y, w, h) in [(200, 180, 128, 128), (0, 0, 64, 40), (470, 500, 42, 12), (0, 0, 512, 512)]:
        cx, cy, cw, ch = proto.roi_crop(x, y, w, h)
        crop = bytes(rnd.randrange(256) for _ in range(cw * ch))
        cp = os.path.join(tmp, "crop.bin")
        open(cp, "wb").write(crop)
        r = subprocess.run([exe, "--paste", cp, str(cx), str(cy), str(cw), str(ch),
                            str(x), str(y), str(w), str(h), tmp],
                           capture_output=True, text=True, env=env)
        full = open(os.path.join(tmp, "full.bin"), "rb").read() if r.returncode == 0 else b""
        roi = open(os.path.join(tmp, "roi.bin"), "rb").read() if r.returncode == 0 else b""
        ref_full = bytearray([proto.ROI_OUTSIDE_LOGIT & 0xFF]) * (512 * 512)
        ref_roi = bytearray()
        for yy in range(h):
            row = crop[(y - cy + yy) * cw + (x - cx):(y - cy + yy) * cw + (x - cx) + w]
            ref_full[(y + yy) * 512 + x:(y + yy) * 512 + x + w] = row
            ref_roi += row
        check(full == bytes(ref_full) and roi == bytes(ref_roi),
              "ROI(%d,%d,%dx%d) 裁剪(%d,%d,%dx%d)" % (x, y, w, h, cx, cy, cw, ch))


class FakeBoardV5(threading.Thread):
    """读 magic 决定头长度（v4 12 / v5 20），记下 ROI 字段；回统计块 v3 + 预处理图。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = None

    def _recv(self, c, n):
        buf = b""
        while len(buf) < n:
            chunk = c.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def run(self):
        c, _ = self.srv.accept()
        magic = struct.unpack("<H", self._recv(c, 2))[0]
        rest = self._recv(c, (proto.CTRL_BYTES_V5 if magic == proto.CTRL_MAGIC_V5
                              else proto.CTRL_BYTES_V4) - 2)
        hdr = struct.pack("<H", magic) + rest
        self._recv(c, proto.STREAM_TOTAL_BYTES)
        self.got = (magic, struct.unpack_from("<HHHH", hdr, 12) if magic == proto.CTRL_MAGIC_V5
                    else None)
        x, y, w, h = self.got[1] or (0, 0, 0, 0)
        cx, cy, cw, ch = proto.roi_crop(x, y, w, h) if w else (0, 0, 0, 0)
        out = struct.pack("<16I", proto.AI_STAT_MAGIC, 3, 0x0009, 512, 10, w * h or 262144,
                          0, 0, 0, 80000, 1, 0, 255, 0, 1, (ch << 16) | cw)
        out += b"".join(proto.iter_packets(bytes(proto.IMG_TOTAL_BYTES)))
        c.sendall(out)
        c.close()


def part3_protocol():
    print("\n=== 3) 协议：v5 头与统计块 v3 ===")
    roi = (200, 180, 128, 96)
    fb = FakeBoardV5()
    fb.start()
    link = imagelink.ImageLink("127.0.0.1", fb.port, timeout=5.0)
    try:
        _imgs, _t, _r, st = link.round_trip(bytes(proto.IMG_TOTAL_BYTES), 0,
                                            flags=proto.FLAG_ECHO_PROC | proto.FLAG_STATS, roi=roi)
        fb.join(2)
        want_crop = proto.roi_crop(*roi)
        check(fb.got == (proto.CTRL_MAGIC_V5, roi), "板端收到 v5 头且 ROI 字段原样", str(fb.got))
        check((st["crop_w"], st["crop_h"]) == want_crop[2:] and st["total"] == 128 * 96,
              "统计块 v3 解出裁剪尺寸与 ROI 面积", "%dx%d" % (st["crop_w"], st["crop_h"]))
    except Exception as e:                       # noqa: BLE001
        check(False, "v5 往返", "异常：%s" % e)
    finally:
        link.close()


def part4_compare():
    print("\n=== 4) confidence.roi_compare（ROI 结果 vs 整图 logit） ===")
    rnd = random.Random(3)
    full = bytes(rnd.randrange(256) for _ in range(confidence.N_PIX))
    roi = (100, 50, 64, 32)
    x, y, w, h = roi
    pasted = bytearray([proto.ROI_OUTSIDE_LOGIT + 128]) * confidence.N_PIX
    for yy in range(h):
        pasted[(y + yy) * 512 + x:(y + yy) * 512 + x + w] = full[(y + yy) * 512 + x:(y + yy) * 512 + x + w]
    n, bad = confidence.roi_compare(bytes(pasted), full, roi)
    check(n == w * h and bad == 0, "完全一致时", "比对 %d 像素，差异 %d" % (n, bad))
    pasted[(y + 5) * 512 + x + 7] ^= 1
    n, bad = confidence.roi_compare(bytes(pasted), full, roi)
    check(bad == 1, "改一个像素能查出来", "差异 %d" % bad)


def main():
    if proto.SELF_CHECK_ERRORS:
        check(False, "proto self-check", str(proto.SELF_CHECK_ERRORS))
    tmp = tempfile.mkdtemp(prefix="roi_verify_")
    try:
        built = build_harness(tmp)
        if built is None:
            check(False, "编译 ai_view_dump（需要 gcc）")
        else:
            part1_crop(*built)
            part2_paste(built[0], built[1], tmp)
        part3_protocol()
        part4_compare()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + "=" * 62)
    if failures:
        print("ROI_VERIFY FAILED: %d 项 —— %s" % (len(failures), failures))
        return 1
    print("ROI_VERIFY OK: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
