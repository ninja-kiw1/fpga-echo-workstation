# -*- coding: utf-8 -*-
"""第 0 步: 上板测量整网时间分解（ap = PL 计算 / put / get / zero / other）。

用法: python _profile_run.py
前提: PL 里已经加载我们的 ai_sys 比特流（load_ai_sys.py）。
"""
import os
import subprocess
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
HERE = r"E:/fpga/my_project/tcp_image_rx/ai/net"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
REMOTE = "/root/tcp_image_rx"
RDIR = REMOTE + "/ai_c"
GCC = ("E:/fpga/vivado/2025.2.1/gnu/aarch64/nt/aarch64-linux/bin/"
       "aarch64-linux-gnu-gcc.exe")
PLD512 = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
          r"unet_network/vectors/images/im0002")

SRC_FILES = ["src/unet_ai.c", "src/unet_ai.h", "src/unet_net.h",
             "src/unet_net_data.c", "src/ai_view.c", "src/ai_view.h",
             "test/unet_selftest.c"]


def cross_check():
    ok = True
    for rel in ("src/unet_ai.c", "test/unet_selftest.c"):
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


def main():
    cross_check()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    c.exec_command("mkdir -p %s" % RDIR)[1].read()

    print("\n=== upload + build ===")
    sftp = c.open_sftp()
    for rel in SRC_FILES:
        sftp.put(os.path.join(PROJ, rel), RDIR + "/" + os.path.basename(rel))
    sftp.put(os.path.join(HERE, "vectors_small", "golden", "x.bin"), RDIR + "/v32_x.bin")
    sftp.put(os.path.join(HERE, "vectors_small", "golden", "final.bin"), RDIR + "/v32_final.bin")
    sftp.put(os.path.join(PLD512, "input.bin"), RDIR + "/v512_x.bin")
    sftp.put(os.path.join(PLD512, "golden", "final.bin"), RDIR + "/v512_final.bin")
    sftp.close()

    _i, o, _e = c.exec_command(
        "cd %s && gcc -O2 -Wall -Wextra -std=c99 -D_GNU_SOURCE -I. "
        "unet_ai.c unet_net_data.c unet_selftest.c -o unet_selftest 2>&1; echo RC=$?" % RDIR,
        timeout=300)
    print(o.read().decode(errors="replace").rstrip())

    for tag, x, g, shift, env in (
            ("512 A-ov overlap", "v512_x.bin", "v512_final.bin", 0, ""),
            ("512 NOAOV control", "v512_x.bin", "v512_final.bin", 0, "UNET_NOAOV=1 "),
            ("32x32", "v32_x.bin", "v32_final.bin", 4, "")):
        print("\n=== profile %s (shift=%d) ===" % (tag, shift))
        _i, o, _e = c.exec_command(
            "cd %s && UNET_PROFILE=1 %stimeout 1200 ./unet_selftest %s %s 1 %d 2>&1; echo RC=$?"
            % (RDIR, env, x, g, shift), timeout=1260)
        print(o.read().decode(errors="replace").rstrip())

    c.close()
    print("\nPROFILE_DONE")


if __name__ == "__main__":
    main()
