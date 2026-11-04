# -*- coding: utf-8 -*-
"""板上跑 unet_selftest 的小工具(带返回码与行缓冲), 用来定位崩溃。

  python _run_selftest.py <shift> [iters] [env...]
例: python _run_selftest.py 4 1 UNET_DEBUG=1
"""
import sys

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
RDIR = "/root/tcp_image_rx/ai_c"
PLD = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
       r"unet_network/vectors/images/im0002")

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                                 # noqa: BLE001
    pass


def main():
    shift = sys.argv[1] if len(sys.argv) > 1 else "4"
    iters = sys.argv[2] if len(sys.argv) > 2 else "1"
    envs = " ".join(sys.argv[3:])

    x = "x512.bin" if shift == "0" else "x32.bin"
    g = "g512.bin" if shift == "0" else "g32.bin"

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=8,
              banner_timeout=8, auth_timeout=8,
              look_for_keys=False, allow_agent=False)
    # stdbuf 强制行缓冲: 崩了也能看到最后几行; env 显式带上环境变量
    cmd = ("cd %s && timeout 120 env %s stdbuf -oL -eL ./unet_selftest %s %s %s %s 2>&1; "
           "echo SELFTEST_RC=$?" % (RDIR, envs, x, g, iters, shift))
    _i, o, _e = c.exec_command(cmd, timeout=200)
    try:
        print(o.read().decode(errors="replace").rstrip())
    except Exception as ex:                       # noqa: BLE001
        print("[read timeout: %r]" % (ex,))
    print(sh_dmesg(c))
    c.close()


def sh_dmesg(c):
    _i, o, _e = c.exec_command("dmesg | tail -6", timeout=30)
    try:
        return "--- dmesg ---\n" + o.read().decode(errors="replace").rstrip()
    except Exception:                             # noqa: BLE001
        return ""


if __name__ == "__main__":
    main()
