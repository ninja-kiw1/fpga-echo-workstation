# -*- coding: utf-8 -*-
"""F4: 全分辨率(条带)上板验证。

两件事:
  1) **档位回归**: shift=4(32x32) 跑条带调度器, 和 P4 那份已验证的 golden 对账
     —— 这一步把"条带逻辑"和"512 正确性"分开定位。
  2) **全分辨率**: shift=0(512x512) 跑, 和 PLD 自己的 golden/final.bin 比对。

用法: python run_fullres.py
"""
import os
import subprocess
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
HERE = r"E:/fpga/my_project/tcp_image_rx/ai/net"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
REMOTE = "/root/tcp_image_rx"
RDIR = REMOTE + "/ai_c"
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"
GCC = ("E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/"
       "aarch64-linux-gnu-gcc.exe")

PLD512 = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
          r"unet_network/vectors/images/im0002")

SRC_FILES = ["src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
             "src/unet_net_data.c", "src/ai_view.c", "src/ai_view.h",
             "test/unet_selftest.c"]


def cross_check():
    ok = True
    for rel in ("src/unet_ai.c", "src/unet_net_data.c", "test/unet_selftest.c"):
        p = os.path.join(PROJ, rel)
        r = subprocess.run([GCC, "-O2", "-Wall", "-Wextra", "-std=c99", "-D_GNU_SOURCE",
                            "-I" + os.path.join(PROJ, "src"), "-c", p,
                            "-o", os.path.join(PROJ, "build_ai", "chk_" + os.path.basename(p) + ".o")],
                           capture_output=True, text=True)
        if r.returncode != 0 or r.stderr.strip():
            ok = False
            print("### %s" % rel)
            print(r.stdout + r.stderr)
    print("[cross] %s" % ("clean" if ok else "HAS WARNINGS"))
    return ok


def main():
    cross_check()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    _i, o, _e = c.exec_command("mkdir -p %s" % RDIR)
    o.read()

    print("\n=== upload + build ===")
    sftp = c.open_sftp()
    for rel in SRC_FILES:
        sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
    sftp.put(os.path.join(PROJ, "test", "unet_selftest.c"), RDIR + "/unet_selftest.c")

    # 两组 golden
    print("[up] 32x32 golden (P4 已验证)")
    sftp.put(os.path.join(HERE, "vectors_small", "golden", "x.bin"), RDIR + "/v32_x.bin")
    sftp.put(os.path.join(HERE, "vectors_small", "golden", "final.bin"), RDIR + "/v32_final.bin")
    print("[up] 512 golden (PLD 原始)")
    sftp.put(os.path.join(PLD512, "input.bin"), RDIR + "/v512_x.bin")
    sftp.put(os.path.join(PLD512, "golden", "final.bin"), RDIR + "/v512_final.bin")
    sftp.close()

    _i, o, _e = c.exec_command(
        "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
        "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; echo RC=$?" % RDIR,
        timeout=300)
    print(o.read().decode(errors="replace").rstrip())

    print("\n=== [1/2] 档位回归: shift=4 (32x32) vs P4 golden ===")
    _i, o, _e = c.exec_command(
        "cd %s && timeout 300 ./unet_selftest v32_x.bin v32_final.bin 1 4 2>&1; echo RC=$?" % RDIR,
        timeout=330)
    t32 = o.read().decode(errors="replace")
    print(t32.rstrip())

    print("\n=== [2/2] 全分辨率: shift=0 (512x512) vs PLD golden ===")
    _i, o, _e = c.exec_command(
        "cd %s && timeout 1200 ./unet_selftest v512_x.bin v512_final.bin 1 0 2>&1; echo RC=$?" % RDIR,
        timeout=1260)
    t512 = o.read().decode(errors="replace")
    print(t512.rstrip())

    sftp = c.open_sftp()
    with sftp.open("%s/fullres_32.log" % RDIR, "w") as fh:
        fh.write(t32)
    with sftp.open("%s/fullres_512.log" % RDIR, "w") as fh:
        fh.write(t512)
    sftp.close()
    c.close()

    ok = ("PASS" in t32) and ("PASS" in t512)
    print("\nFULLRES_DONE ok=%s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
