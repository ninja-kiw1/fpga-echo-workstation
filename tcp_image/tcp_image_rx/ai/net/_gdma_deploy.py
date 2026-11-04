# -*- coding: utf-8 -*-
"""把 GDMA 读回接进业务的分步部署/验证。

  python _gdma_deploy.py check    # 本机用 aarch64 交叉编译器做语法/告警检查
  python _gdma_deploy.py mod      # 上传 + 板上编译 plbdma.ko + insmod + 自检
  python _gdma_deploy.py golden   # 跑 run_fullres.py: golden 逐字节 + 分段计时(先看收益)
  python _gdma_deploy.py deploy   # 重编服务端并重启 systemd 服务
  python _gdma_deploy.py state    # 板子/模块/服务现状
"""
import os
import subprocess
import sys
import time

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
KMOD_LOCAL = os.path.join(PROJ, "kmod")
GCC = ("E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/"
       "aarch64-linux-gnu-gcc.exe")
KDIR = "/root/linux-xlnx"
KMOD = "/root/tcp_image_rx/kmod"
REMOTE = "/root/tcp_image_rx"
LOG = KMOD + "/_plbdma_build.log"


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=8,
              banner_timeout=8, auth_timeout=8,
              look_for_keys=False, allow_agent=False)
    return c


def sh(c, cmd, timeout=60, tol=45):
    _i, o, _e = c.exec_command(cmd, timeout=timeout)
    try:
        return o.read().decode(errors="replace")
    except Exception as ex:                       # noqa: BLE001
        return "[read timeout/err after %ds: %r]" % (tol, ex)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "state"

    if stage == "check":
        ok = True
        for rel in ("src/unet_ai.c", "src/unet_net_data.c", "test/unet_selftest.c"):
            p = os.path.join(PROJ, rel)
            r = subprocess.run([GCC, "-O2", "-Wall", "-Wextra", "-std=c99",
                                "-D_GNU_SOURCE", "-fsyntax-only",
                                "-I" + os.path.join(PROJ, "src"), p],
                               capture_output=True, text=True)
            tag = "clean" if (r.returncode == 0 and not r.stderr.strip()) else "PROBLEM"
            if tag != "clean":
                ok = False
            print("[cross] %-24s %s" % (rel, tag))
            if r.stderr.strip():
                print(r.stdout + r.stderr)
        print("CHECK_%s" % ("OK" if ok else "FAIL"))
        return 0 if ok else 1

    c = connect()
    print("ssh ok", flush=True)

    if stage == "mod":
        sftp = c.open_sftp()
        for f in ("plbdma.c", "makefile"):
            sftp.put(os.path.join(KMOD_LOCAL, f), KMOD + "/" + f)
        sftp.close()
        print("[up] plbdma.c + makefile", flush=True)
        print(sh(c, "cd %s && rm -f %s && nohup sh -c 'make -C %s M=%s modules "
                    "> %s 2>&1; echo BUILD_RC=$? >> %s' >/dev/null 2>&1 & echo STARTED"
                 % (KMOD, LOG, KDIR, KMOD, LOG, LOG), 30))
        for i in range(8):
            time.sleep(12)
            out = sh(c, "tail -2 %s 2>/dev/null; ls -l %s/plbdma.ko 2>/dev/null || echo NO_KO"
                     % (LOG, KMOD), 30)
            print("[poll %d]" % i, out.rstrip(), flush=True)
            if "BUILD_RC" in out:
                break
        print("\n--- 编译日志 ---", flush=True)
        print(sh(c, "cat %s" % LOG, 60).rstrip(), flush=True)
        print("\n--- insmod ---", flush=True)
        print(sh(c, "rmmod plbdma 2>/dev/null; cd %s && insmod plbdma.ko && "
                    "echo INSMOD_OK; sleep 1; ls -l /dev/plbdma; "
                    "dmesg | grep -a plbdma | tail -6" % KMOD, 60).rstrip(), flush=True)

    elif stage == "golden":
        env = os.environ.copy()
        r = subprocess.run([r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe",
                            os.path.join(PROJ, "ai", "net", "run_fullres.py")],
                           cwd=os.path.join(PROJ, "ai", "net"),
                           capture_output=True, text=True, timeout=600, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        with open(os.path.join(PROJ, "ai", "net", "_gdma_golden.log"), "w") as fh:
            fh.write(out)
        print(out.rstrip(), flush=True)

    elif stage == "deploy":
        sftp = c.open_sftp()
        for name in sorted(os.listdir(os.path.join(PROJ, "src"))):
            if name.endswith((".c", ".h")):
                sftp.put(os.path.join(PROJ, "src", name), "%s/src/%s" % (REMOTE, name))
        sftp.put(os.path.join(PROJ, "makefile"), REMOTE + "/makefile")
        sftp.close()
        print("[up] src/*.c,*.h + makefile", flush=True)
        print(sh(c, "cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1 | tail -6"
                 % REMOTE, 600, 500).rstrip(), flush=True)
        print(sh(c, "systemctl restart tcp-image-server; sleep 4; "
                    "systemctl is-active tcp-image-server; "
                    "ss -ltn 2>/dev/null | grep 5060", 60).rstrip(), flush=True)

    elif stage == "boot":
        sftp = c.open_sftp()
        sftp.put(os.path.join(KMOD_LOCAL, "plbdma.service"),
                 "/etc/systemd/system/plbdma.service")
        sftp.close()
        print("[up] plbdma.service", flush=True)
        # 服务进程开着 /dev/plbdma -> 模块引用计数非 0, 必须先停服务才能 rmmod
        print(sh(c, "systemctl stop tcp-image-server; sleep 2; pkill -x tcp_image_serve; "
                    "sleep 1; rmmod plbdma 2>&1; lsmod | grep plbdma || echo MOD_GONE",
                 60).rstrip(), flush=True)
        print(sh(c, "K=$(uname -r); mkdir -p /lib/modules/$K/extra; "
                    "cp -f %s/plbdma.ko /lib/modules/$K/extra/ && echo COPIED; "
                    "depmod -a && echo DEPMOD_OK; "
                    "systemctl daemon-reload; systemctl enable plbdma.service; "
                    "systemctl restart plbdma.service; sleep 2; "
                    "systemctl is-active plbdma.service; "
                    "systemctl is-enabled plbdma.service; ls -l /dev/plbdma; "
                    "dmesg | grep -a plbdma | tail -4" % KMOD, 120, 100).rstrip(), flush=True)
        print(sh(c, "systemctl restart tcp-image-server; sleep 4; "
                    "systemctl is-active tcp-image-server; "
                    "ss -ltn 2>/dev/null | grep 5060", 60).rstrip(), flush=True)

    elif stage == "quick":
        # 直接打正在跑的 systemd 服务: 连发 3 帧, 看真实往返
        UI = r"E:/fpga/my_project/tcp_image_ui"
        PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"
        for flags in ("proc,mask,overlay,stats", "noimage,stats"):
            print("\n--- PC 端 flags=%s (3 帧) ---" % flags, flush=True)
            r = subprocess.run([PY, os.path.join(UI, "imagelink.py"), HOST, "5060",
                                "test.ppm", "--mode", "0", "--flags", flags,
                                "--ai-res", "512", "--repeat", "3"],
                               cwd=UI, capture_output=True, text=True, timeout=300)
            print((r.stdout or "").rstrip(), flush=True)
            if (r.stderr or "").strip():
                print("[stderr]", (r.stderr or "").strip()[:800], flush=True)

    elif stage == "state":
        print(sh(c, "uptime; lsmod | grep -E 'plbdma|plbram'; ls -l /dev/plbdma 2>&1; "
                    "dmesg | grep -a plbdma | tail -4; "
                    "systemctl is-active tcp-image-server; "
                    "cat /sys/class/fpga_manager/fpga0/state", 40))

    else:
        print("unknown stage: %s" % stage)

    c.close()
    print("\nGDMA_DEPLOY_%s_DONE" % stage.upper(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
