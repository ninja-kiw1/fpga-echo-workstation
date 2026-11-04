# -*- coding: utf-8 -*-
"""P5-2: 上板验证 C 驱动(unet_ai.c) 与 golden 一致, 并做 C vs Python 计时。

⚠ 已由 `run_fullres.py` 覆盖(后者同时做档位回归与全分辨率对账)。
   这里保留 shift=4(32x32) 这一档, 当作"改了驱动后先跑一遍"的快速回归。

用法: python run_p5_c.py [vectors目录...]   默认两个 P4 向量集都跑
"""
import os
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
HERE = r"E:/fpga/my_project/tcp_image_rx/ai/net"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
REMOTE = "/root/tcp_image_rx"
RDIR = REMOTE + "/ai_c"
GCC = ("E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/"
       "aarch64-linux-gnu-gcc.exe")

VECSETS = sys.argv[1:] or [
    os.path.join(HERE, "vectors_small"),
    os.path.join(HERE, "vectors_small_im0001"),
]

SRC_FILES = ["src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
             "src/unet_net_data.c", "test/unet_selftest.c"]


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    _i, o, _e = c.exec_command("mkdir -p %s" % RDIR)
    o.read()

    sftp = c.open_sftp()
    # Python 调度器也要传最新版(基准对比用)
    sftp.put(os.path.join(HERE, "unet_board.py"), REMOTE + "/unet_board.py")
    for rel in SRC_FILES:
        sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
        print("[up]", rel)

    print("\n=== build on board ===")
    build = ("cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
             "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; echo RC=$?" % RDIR)
    _i, o, _e = c.exec_command(build, timeout=300)
    print(o.read().decode(errors="replace").rstrip())

    rc = 0
    for vec in VECSETS:
        tag = os.path.basename(vec.rstrip("/\\"))
        # 把 golden 的 x.bin / final.bin 传上去
        for name in ("x.bin", "final.bin"):
            sftp.put(os.path.join(vec, "golden", name),
                     "%s/golden_%s_%s" % (RDIR, tag, name))
        print("\n=== %s : C driver vs golden (shift=4, 32x32) ===" % tag)
        cmd = ("cd %s && ./unet_selftest golden_%s_x.bin golden_%s_final.bin 200 4 2>&1; echo RC=$?"
               % (RDIR, tag, tag))
        _i, o, _e = c.exec_command(cmd, timeout=300)
        out = o.read().decode(errors="replace")
        print(out.rstrip())
        if "PASS" not in out:
            rc = 1

    # Python 版调度器基准(same work, no dumps)
    print("\n=== python scheduler benchmark (same vectors_small) ===")
    _i, o, _e = c.exec_command(
        "cd %s && timeout 300 python3 unet_board.py vectors_small /tmp/bo --bench 30 2>&1 | tail -3"
        % REMOTE, timeout=330)
    print(o.read().decode(errors="replace").rstrip())

    sftp.close()
    c.close()
    print("\nP5C_DONE rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
