# -*- coding: utf-8 -*-
"""plbram 内核模块: 上传 / 检查内核源码 / 板上编译 / 基准对比。

用法:
  python _plbram_setup.py check     # 上传代码, 报内核源码是否就位
  python _plbram_setup.py build     # 板上后台编译 (第一次 10-30 min)
  python _plbram_setup.py status    # 看编译日志尾部
  python _plbram_setup.py bench     # insmod + 跑 _plbram_bench (对比两种映射)
"""
import os
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
KMOD_LOCAL = os.path.join(PROJ, "kmod")
NET_LOCAL = os.path.join(PROJ, "ai", "net")
KDIR = "/root/linux-xlnx"
KMOD_REMOTE = "/root/tcp_image_rx/kmod"
RDIR = "/root/tcp_image_rx/ai_c"
LOG = KMOD_REMOTE + "/_build.log"


def sh(c, cmd, timeout=120):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    return o.read().decode(errors="replace"), e.read().decode(errors="replace")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)

    sh(c, "mkdir -p %s %s" % (KMOD_REMOTE, RDIR))
    sftp = c.open_sftp()
    for f in ("plbram.c", "makefile", "build_on_board.sh", "README.md", "bc"):
        sftp.put(os.path.join(KMOD_LOCAL, f), KMOD_REMOTE + "/" + f)
    sftp.put(os.path.join(NET_LOCAL, "_plbram_bench.c"), RDIR + "/_plbram_bench.c")
    try:
        sftp.put(os.path.join(KMOD_LOCAL, "plbram.service"),
                 "/etc/systemd/system/plbram.service")
        print("[up] /etc/systemd/system/plbram.service")
    except IOError as e:
        print("[warn] service file: %s" % e)
    sftp.close()
    print("[up] kmod/* + ai_c/_plbram_bench.c")

    if mode == "check":
        out, _ = sh(c, "ls -d %s 2>/dev/null && echo SRC_PRESENT || echo SRC_MISSING; "
                       "ls %s 2>/dev/null | head -5; "
                       "echo '--- 板载工具 ---'; gcc --version | head -1; "
                       "df -h / | tail -1" % (KDIR, KDIR))
        print(out)

    elif mode == "install-bc":
        # 板上没有 bc, 而 Kbuild 靠它生成 timeconst.h -> 装我们的替身
        out, err = sh(c, "install -m 0755 %s/bc /usr/local/bin/bc && "
                         "echo INSTALLED; command -v bc; "
                         "echo 250 | bc -q %s/kernel/time/timeconst.bc > /tmp/tc.h; echo RC=$?; "
                         "head -14 /tmp/tc.h; echo '--- tail ---'; tail -6 /tmp/tc.h"
                      % (KMOD_REMOTE, KDIR))
        print(out)
        if err.strip():
            print("[stderr]", err.strip())

    elif mode == "build":
        out, _ = sh(c, "cd %s && nohup sh build_on_board.sh > %s 2>&1 & echo STARTED"
                    % (KMOD_REMOTE, LOG))
        print(out)
        print("后台编译中; 用 `python _plbram_setup.py status` 看进度")

    elif mode == "status":
        out, _ = sh(c, "tail -25 %s 2>/dev/null; echo '--- 进程 ---'; "
                       "pgrep -x make >/dev/null && echo MAKE_RUNNING || echo MAKE_IDLE" % LOG)
        print(out)

    elif mode == "bench":
        out, _ = sh(c, "rmmod plbram 2>/dev/null; "
                       "insmod %s/plbram.ko && echo INSMOD_OK; "
                       "dmesg | tail -3; "
                       "if [ ! -e /dev/plbram ]; then "
                       "  M=$(awk '/plbram/{print $1}' /proc/misc); "
                       "  [ -n \"$M\" ] && mknod /dev/plbram c 10 $M && echo MKNOD_OK; fi; "
                       "ls -l /dev/plbram" % KMOD_REMOTE)
        print(out)
        out, _ = sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE "
                       "_plbram_bench.c -o _plbram_bench 2>&1 && echo BUILD_OK "
                       "&& ./_plbram_bench 2>&1; echo RC=$?" % RDIR, timeout=300)
        print(out)

    elif mode == "bench-ddr":
        # 把同一个模块改映射 PS 自己的 DDR(0x40000000, 不过 PL), 看"读"会不会变快
        # —— 用来判断读的瓶颈是在 PS 侧映射语义还是在 PL 通路。
        out, _ = sh(c, "rmmod plbram 2>/dev/null; "
                       "insmod %s/plbram.ko base=0x40000000 && echo INSMOD_DDR_OK; "
                       "dmesg | tail -2" % KMOD_REMOTE)
        print(out)
        out, _ = sh(c, "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE "
                       "_plbram_bench.c -o _plbram_bench 2>&1 && echo BUILD_OK "
                       "&& ./_plbram_bench 0x40000000 2>&1; echo RC=$?" % RDIR, timeout=300)
        print(out)
        out, _ = sh(c, "rmmod plbram; insmod %s/plbram.ko && echo RESTORED_DEFAULT_BASE"
                    % KMOD_REMOTE)
        print(out)

    elif mode == "install-boot":
        # 把模块放进 /lib/modules/<ver>/extra 并做成开机自加载
        out, err = sh(c, "K=$(uname -r); mkdir -p /lib/modules/$K/extra; "
                         "cp -f %s/plbram.ko /lib/modules/$K/extra/ && echo COPIED; "
                         "depmod -a && echo DEPMOD_OK; "
                         "systemctl daemon-reload; systemctl enable plbram.service; "
                         "systemctl restart plbram.service; sleep 1; "
                         "systemctl is-active plbram.service; ls -l /dev/plbram; "
                         "dmesg | tail -3" % KMOD_REMOTE)
        print(out)
        if err.strip():
            print("[stderr]", err.strip())

    elif mode == "status-boot":
        out, _ = sh(c, "systemctl status plbram.service --no-pager 2>&1 | head -12; "
                       "echo '--- boot? ---'; systemctl is-enabled plbram.service")
        print(out)

    else:
        print("unknown mode: %s" % mode)

    c.close()
    print("PLBRAM_DONE")


if __name__ == "__main__":
    main()
