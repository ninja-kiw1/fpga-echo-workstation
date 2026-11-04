# -*- coding: utf-8 -*-
"""从头把 AI 识别链路的每一步重新量一遍（GDMA 落地之后的新基线）。

  python _prof_all.py state     # 现状: 板子/模块/服务/PL
  python _prof_all.py ai        # 板端 AI 内部: shift 0/4 各跑 3 次(带 golden) + 逐节点
  python _prof_all.py e2e       # 全链路: 手动起服务(UNET_PROFILE=1 -v), 6 帧
                                #   512 全回传 / 512 只算不传 / 256 / 128 / 64 / 32 只算不传
  python _prof_all.py sweep     # 上位机分辨率扫描(打 systemd 服务, 每档 3 帧)
"""
import os
import re
import subprocess
import sys
import time

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
UI = r"E:/fpga/my_project/tcp_image_ui"
NET = os.path.join(PROJ, "ai", "net")
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"
GCC = ("E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/"
       "aarch64-linux-gnu-gcc.exe")
REMOTE = "/root/tcp_image_rx"
RDIR = REMOTE + "/ai_c"
RLOG = "/root/prof_all.log"
SH = "/root/_prof_all_start.sh"

# 板上输出可能带非 GBK 字符(GBK 控制台会直接抛 UnicodeEncodeError 把整个脚本打断)
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                 # noqa: BLE001
    pass


def sh(c, cmd, timeout=60, tol=45):
    _i, o, _e = c.exec_command(cmd, timeout=timeout)
    try:
        return o.read().decode(errors="replace")
    except Exception as ex:                       # noqa: BLE001
        return "[read timeout/err after %ds: %r]" % (tol, ex)


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=8,
              banner_timeout=8, auth_timeout=8,
              look_for_keys=False, allow_agent=False)
    return c


def client(args, timeout=200):
    p = subprocess.run([PY, os.path.join(UI, "imagelink.py"), HOST, "5060", "test.ppm"]
                       + args, cwd=UI, capture_output=True, text=True, timeout=timeout)
    return (p.stdout or "").rstrip(), (p.stderr or "").rstrip()


def tail(c, n=120):
    return sh(c, "tail -n %d %s" % (n, RLOG), 60)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "state"

    if stage == "check":
        ok = True
        for rel in ("src/unet_ai.c", "test/unet_selftest.c"):
            p = os.path.join(PROJ, rel)
            r = subprocess.run([GCC, "-O2", "-Wall", "-Wextra", "-std=c99",
                                "-D_GNU_SOURCE", "-fsyntax-only",
                                "-I" + os.path.join(PROJ, "src"), p],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            good = (r.returncode == 0 and not r.stderr.strip())
            ok = ok and good
            print("[cross] %-22s %s" % (rel, "clean" if good else "PROBLEM"))
            if not good:
                print(r.stdout + r.stderr)
        print("CHECK_%s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 1

    if stage == "state":
        c = connect()
        print(sh(c, "date; uname -r; uptime; "
                    "cat /sys/class/fpga_manager/fpga0/state; "
                    "lsmod | grep -E 'plbdma|plbram'; ls -l /dev/plbdma /dev/plbram; "
                    "systemctl is-active tcp-image-server plbdma.service plbram.service; "
                    "grep -E 'pl0_ref ' /sys/kernel/debug/clk/clk_summary | head -2", 40))
        c.close()
        return 0

    if stage == "sweep":
        for res in (512, 256, 128, 64, 32):
            print("\n===== 上位机 res=%d (3 帧, 全回传) =====" % res, flush=True)
            for flags in ("proc,mask,overlay,stats", "noimage,stats"):
                out, err = client(["--mode", "0", "--flags", flags,
                                   "--ai-res", str(res), "--repeat", "3"])
                for line in out.splitlines():
                    if any(k in line for k in ("total=", "AI:", "summary", "LINK ERROR")):
                        print("  [%s] %s" % (flags, line.strip()))
                if err.strip():
                    print("  [stderr]", err.strip()[:400])
        return 0

    c = connect()
    print("ssh ok", flush=True)

    if stage == "ai":
        # 上传源码 + 两个 golden, 板上原生编译自检程序
        sftp = c.open_sftp()
        for rel in ("src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
                    "src/unet_net_data.c", "src/ai_view.c", "src/ai_view.h",
                    "test/unet_selftest.c"):
            sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
        sftp.put(os.path.join(NET, "vectors_small", "golden", "x.bin"), RDIR + "/x32.bin")
        sftp.put(os.path.join(NET, "vectors_small", "golden", "final.bin"),
                 RDIR + "/g32.bin")
        PLD = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
               r"unet_network/vectors/images/im0002")
        sftp.put(os.path.join(PLD, "input.bin"), RDIR + "/x512.bin")
        sftp.put(os.path.join(PLD, "golden", "final.bin"), RDIR + "/g512.bin")
        sftp.close()
        print("[up] 源码 + 两个 golden", flush=True)
        print(sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
                    "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; "
                    "echo BUILD_RC=$?" % RDIR, 300, 280).rstrip(), flush=True)
        for tag, xf, gf, sh4 in (("shift=0 (512x512)", "x512.bin", "g512.bin", 0),
                                 ("shift=4 (32x32)", "x32.bin", "g32.bin", 4)):
            print("\n===== %s =====" % tag, flush=True)
            print(sh(c, "cd %s && UNET_PROFILE=1 timeout 900 ./unet_selftest %s %s 3 %d 2>&1"
                     % (RDIR, xf, gf, sh4), 900, 880).rstrip(), flush=True)

    elif stage == "ovl":
        # 判定实验: DMA 读 BRAM 与 IP 同时在跑, 会不会把数据搞坏
        sftp = c.open_sftp()
        for rel in ("src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
                    "src/unet_net_data.c", "src/ai_view.c", "src/ai_view.h",
                    "test/unet_selftest.c"):
            sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
        PLD = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
               r"unet_network/vectors/images/im0002")
        sftp.put(os.path.join(PLD, "input.bin"), RDIR + "/x512.bin")
        sftp.put(os.path.join(PLD, "golden", "final.bin"), RDIR + "/g512.bin")
        sftp.close()
        print("[up] 源码 + 512 golden", flush=True)
        print(sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
                    "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; "
                    "echo BUILD_RC=$?" % RDIR, 300, 280).rstrip(), flush=True)
        print("\n===== UNET_OVL_PROBE=1 跑整网(512) =====", flush=True)
        print(sh(c, "cd %s && UNET_OVL_PROBE=1 UNET_PROFILE=1 timeout 900 "
                    "./unet_selftest x512.bin g512.bin 1 0 2>&1 | "
                    "grep -aE 'ovl|prof|compare|timing|SELFTEST|ai\\]'" % RDIR,
                 900, 880).rstrip(), flush=True)

    elif stage == "ovl3":
        # 重叠: 上传+编译, 然后跑三档对照
        sftp = c.open_sftp()
        for rel in ("src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
                    "src/unet_net_data.c", "src/ai_view.c", "src/ai_view.h",
                    "test/unet_selftest.c"):
            sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
        PLD = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
               r"unet_network/vectors/images/im0002")
        sftp.put(os.path.join(PLD, "input.bin"), RDIR + "/x512.bin")
        sftp.put(os.path.join(PLD, "golden", "final.bin"), RDIR + "/g512.bin")
        sftp.put(os.path.join(NET, "vectors_small", "golden", "x.bin"), RDIR + "/x32.bin")
        sftp.put(os.path.join(NET, "vectors_small", "golden", "final.bin"),
                 RDIR + "/g32.bin")
        sftp.close()
        print("[up] 源码 + golden", flush=True)
        print(sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
                    "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; "
                    "echo BUILD_RC=$?" % RDIR, 300, 280).rstrip(), flush=True)

        runs = [("A 双缓冲布局 + 不重叠(对照)", "0", "UNET_NOOVL=1"),
                ("B 双缓冲布局 + 重叠", "0", ""),
                ("C 32x32 回归 + 重叠", "4", "")]
        for title, sh4, envs in runs:
            x = "x512.bin" if sh4 == "0" else "x32.bin"
            g = "g512.bin" if sh4 == "0" else "g32.bin"
            print("\n===== %s =====" % title, flush=True)
            print(sh(c, "cd %s && timeout 300 env %s stdbuf -oL -eL ./unet_selftest "
                        "%s %s 1 %s 2>&1 | grep -aE 'prof|compare|timing|SELFTEST|ovl|ai\\]'; "
                        "echo RC=$?" % (RDIR, envs, x, g, sh4), 400, 380).rstrip(),
                  flush=True)

    elif stage == "e2e":
        print(sh(c, "systemctl stop tcp-image-server; sleep 1; pkill -x tcp_image_serve; "
                    "sleep 1; ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE", 40)
              .rstrip(), flush=True)
        sftp = c.open_sftp()
        with sftp.open(SH, "w") as fh:
            fh.write("#!/bin/sh\ncd /root/tcp_image_rx || exit 1\n"
                     "UNET_PROFILE=1 exec ./tcp_image_server -p 5060 -a 0x40000000 "
                     "--no-dma -x 0xA0000000 --echo -n 6 -v --ai-res 512 "
                     "> " + RLOG + " 2>&1\n")
        sftp.close()
        c.exec_command("setsid sh " + SH + " < /dev/null > /dev/null 2>&1 & echo started")[1]
        time.sleep(3)
        print(sh(c, "ss -ltn 2>/dev/null | grep -q 5060 && echo SERVER_ALIVE || echo SERVER_DEAD", 30)
              .rstrip(), flush=True)

        combos = [
            ("512 全回传 proc+mask+overlay+stats", "proc,mask,overlay,stats", 512),
            ("512 只算不传", "noimage,stats", 512),
            ("256 只算不传", "noimage,stats", 256),
            ("128 只算不传", "noimage,stats", 128),
            ("64  只算不传", "noimage,stats", 64),
            ("32  只算不传", "noimage,stats", 32),
        ]
        try:
            for title, flags, res in combos:
                print("\n--- PC: %s ---" % title, flush=True)
                out, err = client(["--mode", "0", "--flags", flags,
                                   "--ai-res", str(res), "--repeat", "1"])
                for line in out.splitlines():
                    if any(k in line for k in ("total=", "AI:", "LINK ERROR")):
                        print("   " + line.strip(), flush=True)
                if err.strip():
                    print("   [stderr] " + err.strip()[:400], flush=True)
            time.sleep(1)
            out = sh(c, "cat " + RLOG, 120)
            with open(os.path.join(NET, "_prof_all_server.log"), "w") as fh:
                fh.write(out)
            print("\n[板端日志已存 _prof_all_server.log, %d 行]" % len(out.splitlines()),
                  flush=True)
        finally:
            print(sh(c, "pkill -x tcp_image_serve; sleep 1; "
                        "systemctl restart tcp-image-server; sleep 3; "
                        "systemctl is-active tcp-image-server; "
                        "ss -ltn 2>/dev/null | grep 5060", 60).rstrip(), flush=True)

        txt = open(os.path.join(NET, "_prof_all_server.log"), errors="replace").read()
        print("\n===== 板端分段摘要 =====", flush=True)
        for rx, label in ((r"^\[rx\] timing.*$", "TCP 收帧"),
                          (r"^\[mem\] expand.*$", "紧包->32bpp"),
                          (r"^\[axil\] round-trip.*$", "PL 预处理往返"),
                          (r"^\[ai\] src=.*$", "AI 整段"),
                          (r"^\[prof\] total=.*$", "AI 内部四段"),
                          (r"^\[prof\] get detail.*$", "get 细分"),
                          (r"^\[prof\] get DMA detail.*$", "get DMA 细分"),
                          (r"^\[prof\] strips=.*$", "搬运字数"),
                          (r"^\[echo\].*$", "回传")):
            hits = [l for l in txt.splitlines() if re.search(rx, l)]
            if hits:
                print("\n-- %s --" % label, flush=True)
                for h in hits:
                    print("   " + h, flush=True)

    else:
        print("unknown stage: %s" % stage)

    c.close()
    print("\nPROF_ALL_%s_DONE" % stage.upper(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
