# -*- coding: utf-8 -*-
"""AI 识别提速调研 —— 第 2 步: 全链路端到端分段时间实测。

做法: 停掉 systemd 服务, 手动前台起服务(带 UNET_PROFILE=1 + -v), 从 PC 端
发两帧(一帧要全部回传 / 一帧只算不传), 把服务自己的分段计时抓回来。

用法: python _prof_e2e.py
产出: _prof_e2e_server.log (板端原始日志) + stdout 摘要
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
LOG = os.path.join(NET, "_prof_e2e_server.log")
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"

RLOG = "/root/prof_e2e.log"
SH = "/root/_prof_start.sh"
SERVER_LINE = ("exec /root/tcp_image_rx/tcp_image_server -p 5060 -a 0x40000000 "
               "--no-dma -x 0xA0000000 --echo -n 2 -v --ai-res 512")


def sh(c, cmd, timeout=60, quiet_timeout=True):
    """跑一条命令并读回输出; 读超时不致命(把已读到的部分返回)。"""
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = ""
    try:
        out = o.read().decode(errors="replace")
    except Exception as ex:                      # noqa: BLE001
        if not quiet_timeout:
            out = "[read timeout: %r]" % (ex,)
    return out


def sh_nb(c, cmd, timeout=60):
    """发一条会 fork 后台的命令, 立刻返回, 不等 channel EOF。"""
    _i, o, _e = c.exec_command(cmd, timeout=timeout)
    return o  # 不读


def client(cfg, save=None, repeat=1):
    args = [PY, os.path.join(UI, "imagelink.py"), HOST, "5060", "test.ppm",
            "--mode", str(cfg["mode"]), "--flags", cfg["flags"],
            "--ai-res", str(cfg["res"]), "--repeat", str(repeat)]
    if save:
        args += ["--save", save]
    p = subprocess.run(args, cwd=UI, capture_output=True, text=True, timeout=300)
    return (p.stdout or "").rstrip(), (p.stderr or "").rstrip(), p.returncode


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=10,
              banner_timeout=10, auth_timeout=10,
              look_for_keys=False, allow_agent=False)
    print("ssh ok", flush=True)

    out = sh(c, "systemctl cat tcp-image-server 2>/dev/null | grep -E '^ExecStart'")
    print("=== 当前服务 ExecStart ===\n%s" % out.rstrip(), flush=True)

    rc = 0
    try:
        print("\n=== 停 systemd 服务 ===", flush=True)
        out = sh(c, "systemctl stop tcp-image-server; sleep 1; "
                    "pkill -x tcp_image_serve; pkill -x tcp_image_server; sleep 1; "
                    "systemctl is-active tcp-image-server; "
                    "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE; echo '(stop done)'")
        print(out.rstrip(), flush=True)

        print("\n=== 手动起服务 (UNET_PROFILE=1 -v) ===", flush=True)
        sftp = c.open_sftp()
        with sftp.open(SH, "w") as fh:
            fh.write("#!/bin/sh\n"
                     "cd /root/tcp_image_rx || exit 1\n"
                     "UNET_PROFILE=1 " + SERVER_LINE + " > " + RLOG + " 2>&1\n")
        sftp.close()
        sh_nb(c, "setsid sh " + SH + " < /dev/null > /dev/null 2>&1 & echo started")
        time.sleep(3)
        out = sh(c, "ss -ltn 2>/dev/null | grep -q 5060 && echo SERVER_ALIVE "
                    "|| echo SERVER_DEAD; wc -l " + RLOG + "; "
                    "ps -eo pid,comm | grep -i tcp_image")
        print(out.rstrip(), flush=True)
        if "SERVER_ALIVE" not in out:
            print("!! 手动服务没起来, 放弃 e2e")
            return 1

        combos = [
            ("A 全回传 proc+mask+overlay+stats",
             dict(mode=0, flags="proc,mask,overlay,stats", res=512),
             os.path.join(NET, "_prof_e2e_overlay.png")),
            ("B 只算不传 noimage+stats",
             dict(mode=0, flags="noimage,stats", res=512), None),
        ]
        for title, cfg, save in combos:
            print("\n--- PC 端: %s ---" % title, flush=True)
            so, se, prc = client(cfg, save)
            print(so, flush=True)
            if se:
                print("[stderr] " + se[:1500], flush=True)
            rc |= prc

        time.sleep(1)
        out = sh(c, "cat " + RLOG, 120)
        with open(LOG, "w") as fh:
            fh.write(out)
        print("\n=== 板端日志已存: %s (%d 行) ===" % (LOG, len(out.splitlines())), flush=True)
    except Exception as ex:                       # noqa: BLE001
        print("!! 异常: %r" % (ex,), flush=True)
        rc = 1
    finally:
        print("\n=== 恢复 systemd 服务 ===", flush=True)
        try:
            out = sh(c, "pkill -x tcp_image_serve; sleep 1; "
                        "systemctl restart tcp-image-server; sleep 4; "
                        "systemctl is-active tcp-image-server; "
                        "ss -ltn 2>/dev/null | grep 5060; echo '(restore done)'")
            print(out.rstrip(), flush=True)
        except Exception as ex:                   # noqa: BLE001
            print("!! 恢复失败: %r" % (ex,), flush=True)
            rc = 1
        c.close()

    print("\n===== 板端分段计时摘要 =====", flush=True)
    pats = [
        (r"^\[(mem|ai)\] tensor BRAM.*$", "BRAM 映射"),
        (r"^\[ai\] tensors.*$", "张量内存"),
        (r"^(?:\[rx\] timing.*)$", "TCP 收帧"),
        (r"^\[ok \] frame \d+ stored.*$", "整帧接收"),
        (r"^\[mem\] expand.*$", "紧包->32bpp"),
        (r"^\[axil\].*$", "PL 预处理往返"),
        (r"^\[ai\] src=.*$", "AI 整段(含降采样+统计)"),
        (r"^\[prof\] total=.*$", "AI 内部四段"),
        (r"^\[prof\] strips=.*$", "AI 搬运字数"),
        (r"^\[echo\].*$", "回传"),
    ]
    if os.path.exists(LOG):
        txt = open(LOG, errors="replace").read()
        for rx, label in pats:
            hits = [l for l in txt.splitlines() if re.search(rx, l)]
            if hits:
                print("\n-- %s --" % label, flush=True)
                for h in hits:
                    print("   " + h, flush=True)
    print("\nE2E_DONE rc=%d" % rc, flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
