# -*- coding: utf-8 -*-
"""E3: 「边缘叠加」(模式 6) 的上板验证。

判据（用**板子自己**的边缘图在 PC 上复算，逐字节比对，排除 PC/PL Sobel 差异）：
  1) 板子跑模式 2 -> 边缘图 edge_board;
  2) 板子跑模式 6 -> 叠加图 out6;
  3) PC 复算 expected = 原图，其中 edge_board >= thr 的像素画白;
  4) out6 与 expected 必须**逐字节相同**。
另外报告 PC 端 Sobel 与板子 Sobel 的接近程度（只是参考，不参与判据）。

用法: python run_edge_test.py [--no-deploy]
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
sys.path.insert(0, UI)

import imageio                                          # noqa: E402
import imagelink                                        # noqa: E402
import proto                                            # noqa: E402

PIX = proto.IMG_WIDTH * proto.IMG_HEIGHT


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
    err = e.read().decode(errors="replace").strip()
    if err:
        print("[stderr]", err)
    c.close()


def compose(orig, edge, thr):
    """PC 端复算：原图 + (edge >= thr 的像素画白)。与 ai_view_edge_overlay 同构。"""
    out = bytearray(orig)
    for i in range(0, len(orig), 3):
        if edge[i] >= thr:
            out[i] = out[i + 1] = out[i + 2] = 255
    return bytes(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    if "--no-deploy" not in sys.argv:
        print("=== deploy ===")
        deploy()

    img, note = imageio.load_image(os.path.join(UI, "test.ppm"))
    print("图片: %s" % note)

    thr, thr_note = imageio.edge_threshold(img)
    print("自动边缘阈值: %s" % thr_note)

    link = imagelink.ImageLink(HOST, 5060)
    fails = 0

    def run(mode, label):
        images, tim, rows, ai = link.round_trip(
            img, mode, flags=proto.FLAG_ECHO_PROC, thr=thr)
        got = images["proc"]
        print("  %-16s %6.1f ms  rows_ok=%d" % (label, tim["total_ms"], rows["proc"]["rows_ok"]))
        return got

    print("\n=== 上板 ===")
    edge_board = run(2, "mode2 边缘图")
    out6 = run(proto.MODE_EDGE_OVERLAY, "mode6 边缘叠加")

    # ---- 判据: 用板子的边缘图在 PC 上复算 ----
    expected = compose(img, edge_board, thr)
    bad = sum(1 for a, b in zip(out6, expected) if a != b)
    n_white = sum(1 for i in range(0, len(out6), 3) if out6[i] == 255
                  and out6[i + 1] == 255 and out6[i + 2] == 255)
    print("\n=== 判据（用板子的边缘图复算）===")
    print("  叠加图与 PC 复算差异 = %d / %d 字节  ->  %s"
          % (bad, len(out6), "PASS" if bad == 0 else "FAIL"))
    print("  画成白边的像素 = %d / %d (%.1f%%)" % (n_white, PIX, 100.0 * n_white / PIX))
    fails += bad != 0

    # ---- 参考: PC 端 Sobel 与板子 Sobel 差多少 ----
    pc_mag = imageio.sobel_magnitude(img)
    diff = sum(1 for i in range(PIX) if pc_mag[i] != edge_board[i * 3])
    agree = sum(1 for i in range(PIX)
                if (pc_mag[i] >= thr) == (edge_board[i * 3] >= thr))
    print("\n=== 参考：PC 端 Sobel vs 板子 Sobel ===")
    print("  逐值相同 %d / %d (%.1f%%)；阈值判决一致 %d / %d (%.1f%%)"
          % (PIX - diff, PIX, 100.0 * (PIX - diff) / PIX,
             agree, PIX, 100.0 * agree / PIX))

    imageio.save_image(os.path.join(OUT, "edge_mode2.png"), edge_board)
    imageio.save_image(os.path.join(OUT, "edge_overlay_mode6.png"), out6)
    print("\n  存图: e2e_out/edge_mode2.png, e2e_out/edge_overlay_mode6.png")

    link.close()
    print("\nEDGE_TEST_DONE fails=%d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
