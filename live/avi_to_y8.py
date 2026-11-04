# -*- coding: utf-8 -*-
"""avi_to_y8.py —— 把任意 AVI（HMC-QU 录屏、自己录的超声画面等）做成板上现场模式（echo_ef mode 3）的 .y8 信号源。
板端服务没有 AVI 解码，file 源只认 /root/echo_src 里的 "Y8V1" 文件（32 字节头 + 逐帧亮度，见 src/live_src.c）；
上位机心超窗口的「现场信号源」下拉框只列 E:/PLD/live/src/index.json 里登记过的 .y8。本脚本两样一起做：
  1. 读 AVI → 灰度（亮度）整屏帧，原分辨率不缩放（板上 live_crop 自己找动的方块、缩到 112）；
  2. 片子太短时整段循环，凑够 --min-seconds（板上默认用前 50 帧找画面；HMC-QU 一段只有约 41 帧）；
  3. 写 live/src/<名字>.y8，并在 live/src/index.json 登记（"固定方块"用 echo_ws.screen_to_112 找到的扇区方块）；
  4. 加 --upload 时传到板上 /root/echo_src（已有且大小相同就不传）。
注意：循环接缝处画面会跳一下，接缝附近那一拍的 EF 不可信；EchoNet 派生的 .y8 不能外传（make_y8.py 头注释）。
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe avi_to_y8.py "D:\\hmcqu\\A4C\\ES0001 _4CH_1.avi" --upload
"""
import argparse
import json
import math
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLD = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLD, "tcp_image", "tcp_image_ui"))
sys.path.insert(0, os.path.join(PLD, "tcp_image", ".board_tools"))

from make_y8 import write_y8  # noqa: E402

HOST = "192.168.1.254"
REMOTE = "/root/echo_src"
NAME_MAX = 63          # job_echo.c 的 live 参数里名字字段 64 字节（含结尾 0）


def read_grey(path):
    """AVI -> [T, H, W] uint8 亮度、fps。cv2 在 Windows 上打不开含中文的路径。"""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("打不开 %s（路径里有中文时请换成纯英文路径）" % path)
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
    out = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        out.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()
    if not out:
        raise ValueError("读不出视频帧：%s" % path)
    return np.stack(out), fps


def y8_name(path):
    base = re.sub(r"[^0-9A-Za-z.-]+", "_",os.path.splitext(os.path.basename(path))[0]).strip("_")
    return base[:NAME_MAX - 3] + ".y8"


def upload(names):
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    try:
        c.exec_command("mkdir -p " + REMOTE)[1].channel.recv_exit_status()
        s = c.open_sftp()
        for n in names:
            local = os.path.join(HERE, "src", n)
            try:
                same = s.stat(REMOTE + "/" + n).st_size == os.path.getsize(local)
            except IOError:
                same = False
            if not same:
                t0 = time.time()
                s.put(local, REMOTE + "/" + n)
                print("  uploaded %s (%.1f MB, %.1f s)" % (n, os.path.getsize(local) / 2 ** 20, time.time() - t0), flush=True)
            else:
                print("  %s already on board" % n, flush=True)
    finally:
        c.close()


def main():
    import echo_ws
    ap = argparse.ArgumentParser()
    ap.add_argument("avi", nargs="+")
    ap.add_argument("--min-seconds", type=float, default=4.0, help="片子短于这个时长就整段循环（默认 4 s）")
    ap.add_argument("--upload", action="store_true", help="传到板上 " + REMOTE)
    a = ap.parse_args()
    os.makedirs(os.path.join(HERE, "src"), exist_ok=True)
    idx_path = os.path.join(HERE, "src", "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if os.path.exists(idx_path) else {}
    done = []
    for path in a.avi:
        v, fps = read_grey(path)
        T, H, W = v.shape
        if W < 112 or H < 112 or W > 4096 or H > 4096:
            raise ValueError("%s：%d×%d 超出板上 112..4096 的范围" % (path, W, H))
        reps = max(1, int(math.ceil(a.min_seconds * fps / T)))
        s = np.concatenate([v] * reps) if reps > 1 else v
        try:
            _, box = echo_ws.screen_to_112(v)
        except ValueError:
            box = dict(x=0, y=0, side=min(W, H))
        fn = y8_name(path)
        write_y8(os.path.join(HERE, "src", fn), s, fps)
        idx[fn] = dict(video=os.path.basename(path), fps=fps, frames=len(s), W=W, H=H, zoom=1.0,
                       x=box["x"], y=box["y"], side=box["side"])
        print("%s: %d×%d, %d frames × %d = %d, %.1f fps, sector square %s"
              % (fn, W, H, T, reps, len(s), fps, (box["x"], box["y"], box["side"])), flush=True)
        done.append(fn)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1)
    if a.upload:
        upload(done)


if __name__ == "__main__":
    main()
