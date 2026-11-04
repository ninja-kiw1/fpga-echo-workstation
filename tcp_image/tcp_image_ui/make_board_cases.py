# -*- coding: utf-8 -*-
"""make_board_cases.py —— P1 1.5：把演示病例放到板子的 SD 卡上（/root/cases），并把板上界面装到 /root/station。

  CT     MSD 原始 .nii.gz（验证 / 训练集，不碰测试集）原样拷过去，板上自己解压、分割
  心超   EchoNet VAL 视频在 PC 上用 OpenCV 解码成灰度帧文件（板上不解码：libjpeg 与 OpenCV 差 2.7% 像素），
         echo_ef 的 128 字节参数也在 PC 上算好（echo_ws.load_config + jobs.echo_params），写进 manifest
根分区空间紧（10-15 实测剩 624 MB），默认只放 1 卷 CT + 2 段心超（约 20 MB）。EchoNet 数据不外传，只放在自己的板子上。

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe make_board_cases.py [--ct spleen_10] [--echo N]
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", ".board_tools"))
import numpy as np  # noqa: E402
import paramiko  # noqa: E402

import echo_ws  # noqa: E402
import jobs  # noqa: E402
import proto  # noqa: E402

PLD = os.path.normpath(os.path.join(HERE, "..", ".."))
MSD = os.path.join(PLD, "Task09_Spleen", "Task09_Spleen", "imagesTr")
ECHONET = os.path.join(PLD, "EchoNet-Dynamic")
TEST_CT = {"spleen_8", "spleen_9", "spleen_13", "spleen_17", "spleen_26", "spleen_32", "spleen_63"}
STATION_FILES = ["board_station.py", "jobs.py", "imagelink.py", "imageio.py", "proto.py"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ct", nargs="*", default=["spleen_10"])
    ap.add_argument("--echo", type=int, default=2)
    a = ap.parse_args()
    out = os.path.join(HERE, "board_cases")
    os.makedirs(out, exist_ok=True)
    manifest = []
    for nm in a.ct:
        if nm in TEST_CT:
            raise SystemExit("%s is in the CT test split" % nm)
        manifest.append(dict(kind="ct", name=nm, file=nm + ".nii.gz", src=os.path.join(MSD, nm + ".nii.gz")))
    conf = echo_ws.load_config()
    scale = proto.MODELS[16]["scale"]
    vids = sorted(r["FileName"][:-4] if r["FileName"].endswith(".avi") else r["FileName"]
                  for r in csv.DictReader(open(os.path.join(ECHONET, "FileList.csv"))) if r["Split"].upper() == "VAL")
    for vid in vids[:a.echo]:
        frames, fps = echo_ws.read_avi(os.path.join(ECHONET, "Videos", vid + ".avi"))
        f = np.ascontiguousarray(frames, np.uint8)
        if f.ndim == 4:
            f = f[..., 0]
        fn = vid + ".u8"
        f.tofile(os.path.join(out, fn))
        manifest.append(dict(kind="echo", name=vid, frames=fn, shape=list(f.shape), fps=float(fps), model=16,
                             params_hex=jobs.echo_params(conf, scale, 16, False, len(f), fps).hex(), src=os.path.join(out, fn)))
    json.dump([{k: v for k, v in m.items() if k != "src"} for m in manifest], open(os.path.join(out, "manifest.json"), "w",
              encoding="utf-8"), ensure_ascii=False, indent=1)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    c.exec_command("mkdir -p /root/cases /root/station")[1].read()
    sf = c.open_sftp()
    for m in manifest:
        sf.put(m["src"], "/root/cases/" + (m.get("file") or m["frames"]))
    sf.put(os.path.join(out, "manifest.json"), "/root/cases/manifest.json")
    for f in STATION_FILES:
        sf.put(os.path.join(HERE, f), "/root/station/" + f)
    sf.close()
    _, o, _ = c.exec_command("du -sh /root/cases /root/station; df -h / | tail -1; cd /root/station && python3 -c 'import jobs, imagelink; print(\"imports ok\")'")
    print(o.read().decode("utf-8", "replace"))
    c.close()


if __name__ == "__main__":
    main()
