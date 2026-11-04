#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
send_image.py —— PC 端图像发送脚本(与板上 tcp_image_server 配套)。

把一张 512x512 的 RGB888 图像切成 512 个"行包"发给开发板。每个行包:

    偏移  长度  字段      说明
    ----  ----  --------  ----------------------------------------
     0     2    row       行号 0..511, 小端
     2     2    length    负载长度, 固定 1536, 小端
     4     2    crc16     CRC-16/CCITT-FALSE(对 1536 字节负载), 小端
     6    1536  payload   该行 512 个像素, 每像素 R,G,B

单包 1542 字节, 整幅 512*1542 = 789504 字节。

只依赖 Python 标准库(不需要 numpy / Pillow):
  * 图像来源三选一: 内置测试图样(--pattern)、PPM P6 文件(--in xxx.ppm)、
    裸 RGB 文件(--in xxx.rgb, 需正好 786432 字节)。
  * 发送前可 --dump 把准备发送的原始图存盘, 便于和板子上的结果逐字节比对。

⚠ 控制台输出一律用英文: 日志被重定向/抓取时中文可能变乱码, 中文只留在注释里。

用法示例
--------
    python send_image.py --selftest                  # 校验 CRC 实现
    python send_image.py --host 192.168.1.10         # 发默认渐变图
    python send_image.py -H 192.168.1.10 -P 5000 --pattern bars -v
    python send_image.py -H 192.168.1.10 --in test.ppm --dump sent.rgb
"""

import argparse
import os
import socket
import struct
import sys
import time

# ----------------------------------------------------------------------
# 与 C 端(image_proto.h / crc16.h)必须逐字对应的协议常量
# ----------------------------------------------------------------------
IMG_W, IMG_H, BPP = 512, 512, 3
ROW_BYTES = IMG_W * BPP              # 1536
IMG_BYTES = ROW_BYTES * IMG_H        # 786432
HDR_BYTES = 6
PKT_BYTES = HDR_BYTES + ROW_BYTES    # 1542
PKT_COUNT = IMG_H                    # 512
STREAM_BYTES = PKT_BYTES * PKT_COUNT # 789504

CRC16_INIT = 0xFFFF
CRC16_CHECK = 0x29B1                 # CRC16("123456789") 的标准值


def crc16_ccitt_false(data, crc=CRC16_INIT):
    """CRC-16/CCITT-FALSE: poly=0x1021, init=0xFFFF, 不反转, 不异或输出。

    与 C 版 src/crc16.c 的逐位实现完全一致(同样 MSB-first)。
    """
    for byte in data:
        crc ^= (byte << 8)
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ----------------------------------------------------------------------
# 测试图样生成
# ----------------------------------------------------------------------

# 标准 8 色条(取 75% 幅度, 避免过曝), 顺序同 SMPTE 彩条
_BAR_COLORS_75 = [
    (191, 191, 191),  # 白
    (191, 191, 0),    # 黄
    (0, 191, 191),    # 青
    (0, 191, 0),      # 绿
    (191, 0, 191),    # 品红
    (191, 0, 0),      # 红
    (0, 0, 191),      # 蓝
    (0, 0, 0),        # 黑
]


def make_gradient():
    """水平渐变(R 随 x) + 垂直渐变(G 随 y) + 固定蓝, 便于肉眼判断行列是否错位。"""
    buf = bytearray(IMG_BYTES)
    for y in range(IMG_H):
        base = y * ROW_BYTES
        g = (y * 255) // (IMG_H - 1)
        for x in range(IMG_W):
            p = base + x * BPP
            buf[p] = (x * 255) // (IMG_W - 1)   # R
            buf[p + 1] = g                      # G
            buf[p + 2] = 128                    # B
    return buf


def make_colorbars():
    """8 条竖向彩条, 每条约 64 像素。"""
    buf = bytearray(IMG_BYTES)
    bar_w = IMG_W // len(_BAR_COLORS_75)        # 64
    for y in range(IMG_H):
        base = y * ROW_BYTES
        for x in range(IMG_W):
            idx = min(x // bar_w, len(_BAR_COLORS_75) - 1)
            r, g, b = _BAR_COLORS_75[idx]
            p = base + x * BPP
            buf[p] = r
            buf[p + 1] = g
            buf[p + 2] = b
    return buf


def make_checker():
    """32x32 像素黑白棋盘, 便于直接看出行列错位/丢行。"""
    buf = bytearray(IMG_BYTES)
    cell = 32
    for y in range(IMG_H):
        base = y * ROW_BYTES
        ycell = y // cell
        for x in range(IMG_W):
            on = ((x // cell) + ycell) & 1
            v = 255 if on else 0
            p = base + x * BPP
            buf[p] = v
            buf[p + 1] = v
            buf[p + 2] = v
    return buf


PATTERNS = {
    "gradient": make_gradient,
    "bars": make_colorbars,
    "checker": make_checker,
}


# ----------------------------------------------------------------------
# 读入外部图像
# ----------------------------------------------------------------------

def load_raw(path, expect=IMG_BYTES):
    """读裸 RGB 文件, 长度必须正好等于期望值。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) != expect:
        raise ValueError("%s: %d bytes, expected %d (%dx%d RGB888)"
                         % (path, len(data), expect, IMG_W, IMG_H))
    return bytearray(data)


def load_ppm(path):
    """读 P6 二进制 PPM(只支持 maxval<=255, 即 1 字节/分量)。"""
    with open(path, "rb") as f:
        data = f.read()

    if data[:2] != b"P6":
        raise ValueError("%s: not a P6 PPM" % path)

    idx = 2
    fields = []
    n = len(data)
    while len(fields) < 3:
        while idx < n and data[idx:idx + 1].isspace():
            idx += 1
        if data[idx:idx + 1] == b"#":                 # 注释行
            while idx < n and data[idx:idx + 1] not in (b"\n", b"\r"):
                idx += 1
            continue
        start = idx
        while idx < n and not data[idx:idx + 1].isspace():
            idx += 1
        fields.append(int(data[start:idx]))

    idx += 1                                          # 跳过 maxval 后的那一个空白
    w, h, maxv = fields
    if maxv > 255:
        raise ValueError("%s: maxval=%d, only 1 byte/channel supported" % (path, maxv))
    if (w, h) != (IMG_W, IMG_H):
        raise ValueError("%s: %dx%d, expected %dx%d" % (path, w, h, IMG_W, IMG_H))

    payload = data[idx:]
    if len(payload) < IMG_BYTES:
        raise ValueError("%s: pixel data truncated (%d < %d)"
                         % (path, len(payload), IMG_BYTES))
    return bytearray(payload[:IMG_BYTES])


# ----------------------------------------------------------------------
# 发送
# ----------------------------------------------------------------------

def build_packet(row, payload):
    """按协议组一个行包。"""
    crc = crc16_ccitt_false(payload)
    return struct.pack("<HHH", row, len(payload), crc) + payload


def send_image(host, port, image, repeat=1, verbose=False, source=None,
               timeout=10.0):
    """连一次 TCP, 发 repeat 幅图像。"""
    for rep in range(repeat):
        t0 = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if source:
                sock.bind((source, 0))
            sock.settimeout(timeout)

            print("[net] connecting to %s:%d ..." % (host, port))
            sock.connect((host, port))
            print("[net] connected, sending frame %d/%d (%d bytes)"
                  % (rep + 1, repeat, STREAM_BYTES))

            sent = 0
            for row in range(PKT_COUNT):
                off = row * ROW_BYTES
                pkt = build_packet(row, image[off:off + ROW_BYTES])
                sock.sendall(pkt)
                sent += len(pkt)
                if verbose and (row + 1) % 64 == 0:
                    print("[net]   %d/%d packets" % (row + 1, PKT_COUNT))

            dt = time.time() - t0
            mbps = (sent * 8.0 / dt / 1e6) if dt > 0 else 0.0
            print("[net] frame %d done: %d bytes in %.3f s (%.2f Mbit/s)"
                  % (rep + 1, sent, dt, mbps))
            if sent != STREAM_BYTES:
                print("[net] WARNING: sent %d bytes, expected %d" % (sent, STREAM_BYTES))
        finally:
            sock.close()


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Send a 512x512 RGB888 image to the board over TCP "
                    "(512 row-packets with per-row CRC16).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-H", "--host", default="192.168.1.10",
                    help="board IP address (default 192.168.1.10)")
    ap.add_argument("-P", "--port", type=int, default=5000,
                    help="board TCP port (default 5000)")
    ap.add_argument("--pattern", choices=sorted(PATTERNS), default="gradient",
                    help="built-in test pattern (default gradient)")
    ap.add_argument("--in", dest="infile", default=None,
                    help="input image: .ppm (P6) or raw .rgb (exactly 786432 bytes)")
    ap.add_argument("--dump", default=None,
                    help="write the exact bytes that will be sent to this file")
    ap.add_argument("--repeat", type=int, default=1,
                    help="send the frame this many times (default 1)")
    ap.add_argument("--source", default=None,
                    help="bind local source IP (useful with several NICs)")
    ap.add_argument("--timeout", type=float, default=10.0,
                    help="socket timeout in seconds (default 10)")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print per-64-packet progress")
    ap.add_argument("--selftest", action="store_true",
                    help="verify CRC16 against the standard check value and exit")
    args = ap.parse_args(argv)

    # ---- 自检: CRC 实现必须与 C 端一致 ----
    if args.selftest:
        got = crc16_ccitt_false(b"123456789")
        print("CRC16(\"123456789\") = 0x%04X (expected 0x%04X) -> %s"
              % (got, CRC16_CHECK, "OK" if got == CRC16_CHECK else "FAIL"))
        print("layout: %dx%d RGB888, %d bytes/frame, %d pkts x %d bytes = %d bytes"
              % (IMG_W, IMG_H, IMG_BYTES, PKT_COUNT, PKT_BYTES, STREAM_BYTES))
        return 0 if got == CRC16_CHECK else 1

    # ---- 准备图像 ----
    if args.infile:
        path = args.infile
        if path.lower().endswith(".ppm"):
            image = load_ppm(path)
        else:
            image = load_raw(path)
        print("[img] loaded %s (%d bytes)" % (path, len(image)))
    else:
        print("[img] generating pattern '%s' ..." % args.pattern)
        image = PATTERNS[args.pattern]()
        print("[img] generated %d bytes" % len(image))

    if len(image) != IMG_BYTES:
        print("[err] image is %d bytes, expected %d" % (len(image), IMG_BYTES))
        return 2

    if args.dump:
        with open(args.dump, "wb") as f:
            f.write(image)
        print("[img] dumped raw image to %s" % args.dump)

    if args.repeat < 1:
        print("[err] --repeat must be >= 1")
        return 2

    try:
        send_image(args.host, args.port, image,
                   repeat=args.repeat, verbose=args.verbose,
                   source=args.source, timeout=args.timeout)
    except OSError as exc:
        print("[err] network error: %s" % exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
