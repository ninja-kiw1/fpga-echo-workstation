# -*- coding: utf-8 -*-
"""调试用: 重传 unet_selftest.c, 板上重编, 跑指定档位并回显全部输出。

用法: python dbg_fullres.py <shift> [输入文件] [golden文件]
"""
import os
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
RDIR = "/root/tcp_image_rx/ai_c"

shift = sys.argv[1] if len(sys.argv) > 1 else "4"
xin = sys.argv[2] if len(sys.argv) > 2 else "v32_x.bin"
gin = sys.argv[3] if len(sys.argv) > 3 else "v32_final.bin"
dbg = sys.argv[4] if len(sys.argv) > 4 else "1"


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    for rel in ("src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h", "src/unet_net_data.c",
                "test/unet_selftest.c"):
        sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
    sftp.close()

    cmd = ("cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
           "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; echo BUILD_RC=$?; "
           "UNET_DEBUG=%s ./unet_selftest %s %s 1 %s > run.log 2>&1; echo RUN_RC=$?; "
           "tail -45 run.log" % (RDIR, dbg, xin, gin, shift))
    _i, o, e = c.exec_command(cmd, timeout=600)
    print(o.read().decode(errors="replace").rstrip())
    err = e.read().decode(errors="replace").rstrip()
    if err:
        print("[stderr]", err)
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
