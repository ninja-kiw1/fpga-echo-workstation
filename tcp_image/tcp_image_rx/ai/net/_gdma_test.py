# -*- coding: utf-8 -*-
"""GDMA 搬数据可行性/带宽探测 —— 分阶段跑, 每阶段单独一次调用, 方便"见好就收"。

  python _gdma_test.py build     # 上传 + 板上原生编译 gdmatest.ko
  python _gdma_test.py stage0    # DDR->DDR 自检(零风险)
  python _gdma_test.py stage1    # BRAM->DDR 读 4KB / 64KB / 256KB  <-- 第一次碰 PL 地址
  python _gdma_test.py stage2    # DDR->BRAM 写 + 回读校验
  python _gdma_test.py state     # 板子还在不在 / 服务状态

设计成"insmod 里把测试跑完然后故意返回 -ENODEV 不常驻", 所以每档都能重复跑,
不用 rmmod, 也不会占着 DMA 通道。
"""
import os
import sys
import time

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
KMOD_LOCAL = os.path.join(PROJ, "kmod")
KDIR = "/root/linux-xlnx"
KMOD = "/root/tcp_image_rx/kmod"
LOG = KMOD + "/_gdma_build.log"


def connect(short=True):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW,
              timeout=8, banner_timeout=8, auth_timeout=8,
              look_for_keys=False, allow_agent=False)
    if short:
        # 板子万一挂死, 别让工具调用无限等下去
        tr = c.get_transport()
        if tr is not None:
            tr.sock.settimeout(20.0)
    return c


def sh(c, cmd, timeout=60, tol=45):
    """跑命令并读回输出。读超时/连接断掉都不致命, 返回可读的字符串。"""
    _i, o, _e = c.exec_command(cmd, timeout=timeout)
    try:
        return o.read().decode(errors="replace")
    except Exception as ex:                       # noqa: BLE001
        return "[read timeout/err after %ds: %r]" % (tol, ex)


def logs(c, n=40):
    return sh(c, "dmesg | grep -a gdmatest | tail -n %d" % n, 40)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "state"

    if stage == "state":
        try:
            c = connect()
        except Exception as ex:                   # noqa: BLE001
            print("!! 连不上板子: %r" % (ex,))
            return 1
        print(sh(c, "uptime; cat /sys/class/fpga_manager/fpga0/state; "
                    "systemctl is-active tcp-image-server; "
                    "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE; "
                    "lsmod | grep -E 'plbram|gdmatest' || echo NO_MOD", 30))
        c.close()
        return 0

    c = connect()
    print("ssh ok", flush=True)

    if stage == "build":
        sftp = c.open_sftp()
        for f in ("gdmatest.c", "makefile"):
            sftp.put(os.path.join(KMOD_LOCAL, f), KMOD + "/" + f)
        sftp.close()
        print("[up] gdmatest.c + makefile", flush=True)
        print(sh(c, "ls -d %s && echo SRC_PRESENT || echo SRC_MISSING" % KDIR,
                 30).rstrip(), flush=True)
        # 后台编译(小模块, 但 modules_prepare 已完成, 通常 <1 min)
        print(sh(c, "cd %s && rm -f %s && nohup sh -c 'make -C %s M=%s modules "
                    "> %s 2>&1; echo BUILD_RC=$? >> %s' > /dev/null 2>&1 & "
                    "echo STARTED" % (KMOD, LOG, KDIR, KMOD, LOG, LOG), 30))
        for i in range(20):
            time.sleep(15)
            out = sh(c, "tail -3 %s 2>/dev/null; ls -l %s/gdmatest.ko 2>/dev/null "
                        "|| echo NO_KO" % (LOG, KMOD), 30)
            print("[poll %d]" % i, out.rstrip(), flush=True)
            if "BUILD_RC" in out or "NO_KO" not in out:
                break
        print("\n--- 完整日志 ---", flush=True)
        print(sh(c, "cat %s" % LOG, 60).rstrip(), flush=True)
        c.close()
        return 0

    if stage == "stage0":
        print(sh(c, "cd %s && insmod gdmatest.ko mode=0; echo INSMOD_RC=$?" % KMOD, 60))
        time.sleep(1)
        print(logs(c), flush=True)

    elif stage == "stage1":
        print("!!! 第一次访问 PL 地址 0xB0000000 —— 若不可达可能挂死板子", flush=True)
        # 先把业务服务停掉: 探测期间不要让别的进程碰同一片 BRAM
        print(sh(c, "systemctl stop tcp-image-server; sleep 1; "
                    "pkill -x tcp_image_serve; sleep 1; "
                    "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE", 40).rstrip(),
              flush=True)
        # ⚠ 只能是 256KB 窗口以内! 越界会读到没有从机的 PL 地址 -> 挂死
        for L in (4096, 65536, 262144):
            print("\n===== BRAM 读 len=%d =====" % L, flush=True)
            out = sh(c, "cd %s && insmod gdmatest.ko mode=1 len=%d; echo INSMOD_RC=$?"
                     % (KMOD, L), 90, 80)
            print(out.rstrip(), flush=True)
            time.sleep(1)
            print(logs(c, 16), flush=True)

    elif stage == "stage2":
        print(sh(c, "cd %s && insmod gdmatest.ko mode=2 len=262144; echo INSMOD_RC=$?"
                 % KMOD, 120, 100))
        time.sleep(1)
        print(logs(c, 30), flush=True)

    elif stage == "stage3":
        print("!!! 真实条带工作量模拟(826 条带 x 写142KB + 读103KB)", flush=True)
        print(sh(c, "cd %s && insmod gdmatest.ko mode=3 iters=826; echo INSMOD_RC=$?"
                 % KMOD, 300, 280))
        time.sleep(1)
        print(logs(c, 24), flush=True)

    elif stage == "restore":
        print(sh(c, "pkill -x tcp_image_serve; sleep 1; "
                    "systemctl restart tcp-image-server; sleep 3; "
                    "systemctl is-active tcp-image-server; "
                    "ss -ltn 2>/dev/null | grep 5060 || echo PORT_FREE; "
                    "cat /sys/class/fpga_manager/fpga0/state", 60).rstrip(), flush=True)

    else:
        print("unknown stage: %s" % stage)

    c.close()
    print("\nGDMA_%s_DONE" % stage.upper(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
