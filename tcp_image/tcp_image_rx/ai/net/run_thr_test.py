# -*- coding: utf-8 -*-
"""T4: 阈值分割门限的端到端验证。

做的事:
  1) 部署新板端程序并重启服务;
  2) 用 test.ppm 以多个阈值跑 mode 1, 每个阈值都和 **PC 端独立复算**的
     "gray >= thr ? 255 : 0"(灰度 = (77R+150G+29B)>>8) 逐字节比对;
  3) 存几张图当证据。

用法: python run_thr_test.py
"""
import os
import subprocess
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
HERE = os.path.join(PROJ, "ai", "net")
UI = r"E:/fpga/my_project/tcp_image_ui"
REMOTE = "/root/tcp_image_rx"
OUT = os.path.join(HERE, "e2e_out")
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"
sys.path.insert(0, UI)

import imageio                                          # noqa: E402
import imagelink                                        # noqa: E402
import proto                                            # noqa: E402


def deploy():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    for name in sorted(os.listdir(os.path.join(PROJ, "src"))):
        if name.endswith((".c", ".h")):
            sftp.put(os.path.join(PROJ, "src", name), "%s/src/%s" % (REMOTE, name))
    sftp.put(os.path.join(PROJ, "makefile"), REMOTE + "/makefile")
    sftp.close()
    _i, o, e = c.exec_command(
        "cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1 | tail -3; "
        "systemctl restart tcp-image-server; sleep 2; systemctl is-active tcp-image-server"
        % REMOTE, timeout=900)
    print(o.read().decode(errors="replace").rstrip())
    err = e.read().decode(errors="replace").rstrip()
    if err:
        print("[stderr]", err)
    c.close()


def main():
    os.makedirs(OUT, exist_ok=True)
    if "--no-deploy" not in sys.argv:
        print("=== deploy ===")
        deploy()

    img, note = imageio.load_image(os.path.join(UI, "test.ppm"))
    gray = imageio._luma_gray(img)                       # 与 PL 同权重
    thr_auto, _hist, thr_note = imageio.auto_threshold(img)
    print("\n图片: %s" % note)
    print("Otsu 推荐阈值: %s" % thr_note)

    link = imagelink.ImageLink(HOST, 5060)
    fails = 0
    for thr in (thr_auto, 40, 100, 160, 200):
        try:
            images, tim, rows, ai = link.round_trip(
                img, 1, flags=proto.FLAG_ECHO_PROC, thr=thr)
        except Exception as e:                          # noqa: BLE001
            print("thr=%3d  LINK ERROR: %s" % (thr, e))
            fails += 1
            continue
        got = images["proc"]
        want = bytearray(proto.IMG_TOTAL_BYTES)
        nwhite = 0
        for k, g in enumerate(gray):
            v = 255 if g >= thr else 0
            if v:
                nwhite += 1
            want[k * 3] = want[k * 3 + 1] = want[k * 3 + 2] = v
        want = bytes(want)
        bad = sum(1 for a, b in zip(got, want) if a != b)
        ok = (bad == 0)
        fails += 0 if ok else 1
        print("thr=%3d  白像素 %6d/%d (%5.1f%%)  与 PC 复算差异 %d 字节  ->  %s   (%.1f ms)"
              % (thr, nwhite, proto.IMG_WIDTH * proto.IMG_HEIGHT,
                 100.0 * nwhite / (proto.IMG_WIDTH * proto.IMG_HEIGHT),
                 bad, "PASS" if ok else "FAIL", tim["total_ms"]))
        if thr in (thr_auto, 100):
            imageio.save_image(os.path.join(OUT, "thr_%d.png" % thr), got)

    link.close()
    print("\nTHR_TEST_DONE fails=%d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
