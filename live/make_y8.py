# -*- coding: utf-8 -*-
"""make_y8.py —— 把 EchoNet VAL 视频做成"模拟采集画面"的 .y8 信号源（服务端 v5.22 echo_ef mode 3 的 file 源）。

采集卡没到之前，用它代替"超声机 / 笔记本的 HDMI 画面"：640 x 480 的整屏亮度（MS2109 的 YUYV 常用档），
屏上有一块动态 B 超画面，四周是静态界面文字（假患者名、深度刻度）和一个每秒变一次的计时器（测试"只取最大运动块"）。
  --zoom 4     112 -> 448 最近邻放大，放在 (96, 16)：板上固定裁切时 112 帧应与原视频逐位相同（live_crop.c 的整数盒滤波）
  --zoom 2.68  112 -> 300 双线性（模拟播放器的非整数缩放），检测自动裁切
TEST 不碰：只取 VAL。EchoNet 数据不外传：.y8 只放在 PC 的 E:/PLD/live/src 和板上 /root/echo_src，不进分享包。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe make_y8.py --videos 3 --zoom 4
"""
import argparse
import json
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLD, "tcp_image", "tcp_image_ui"))
W, H = 640, 480


def write_y8(path, frames, fps):
    n, h, w = frames.shape
    with open(path, "wb") as f:
        f.write(b"Y8V1" + struct.pack("<IIId", w, h, n, fps) + b"\0" * 8)
        f.write(np.ascontiguousarray(frames, np.uint8).tobytes())


def screen(v, fps, zoom):
    """[T,112,112] -> [T,480,640] 整屏亮度 + 真值框 (x, y, side)"""
    if zoom == 4:
        side, x0, y0 = 448, 96, 16
        big = [np.kron(f, np.ones((4, 4), np.uint8)) for f in v]
    else:
        side = int(round(112 * zoom))
        x0, y0 = (W - side) // 2, (H - side) // 2
        big = [np.array(Image.fromarray(f).resize((side, side), Image.BILINEAR)) for f in v]
    base = Image.new("L", (W, H), 12)
    d = ImageDraw.Draw(base)
    d.text((6, 6), "SIMULATED SCREEN", fill=200)
    d.text((6, 22), "PATIENT: TEST^ANON", fill=200)
    d.text((6, 38), "ID 0000000", fill=200)
    d.text((6, 54), "A4C  50 Hz", fill=160)
    for k in range(0, 15):                                  # 深度刻度（右侧，静态）
        y = 20 + k * 30
        d.line((W - 30, y, W - 18, y), fill=180)
        d.text((W - 16, y - 6), str(k), fill=150)
    base = np.array(base)
    out = np.empty((len(v), H, W), np.uint8)
    for t, b in enumerate(big):
        s = base.copy()
        s[y0:y0 + side, x0:x0 + side] = b
        clk = Image.fromarray(s)                            # 计时器：每秒变一次，离 B 超画面远
        ImageDraw.Draw(clk).text((6, H - 18), "%02d:%02d" % (int(t / fps) // 60, int(t / fps) % 60), fill=220)
        out[t] = np.array(clk)
    return out, (x0, y0, side)


def main():
    import echo_ws
    from verify_board_echo_kf import ECHONET, val_videos
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=3)
    ap.add_argument("--skip", type=int, default=0)
    ap.add_argument("--zoom", type=float, default=4.0)
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, "src"), exist_ok=True)
    idx_path = os.path.join(HERE, "src", "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {}
    for name in val_videos(a.skip + a.videos)[a.skip:]:
        v, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", name))
        s, (x0, y0, side) = screen(v, fps, a.zoom)
        fn = "%s_z%s.y8" % (name[:-4], ("%g" % a.zoom).replace(".", "p"))
        write_y8(os.path.join(HERE, "src", fn), s, fps)
        idx[fn] = dict(video=name, fps=fps, frames=len(v), W=W, H=H, zoom=a.zoom, x=x0, y=y0, side=side)
        print(fn, len(v), "frames", fps, "fps, square", (x0, y0, side), flush=True)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1)


if __name__ == "__main__":
    main()
