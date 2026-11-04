# -*- coding: utf-8 -*-
"""F5: 部署条带版板端程序, 用真实眼底图 test.ppm 做分辨率扫描。

用法: python run_res_sweep.py
产出: e2e_out/fullres_mask_<res>.png / fullres_overlay_<res>.png
"""
import os
import subprocess
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
HERE = os.path.join(PROJ, "ai", "net")
UI = r"E:/fpga/my_project/tcp_image_ui"
REMOTE = "/root/tcp_image_rx"
OUT = os.path.join(HERE, "e2e_out")
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"

RES = [512, 256, 128, 64, 32]


def main():
    os.makedirs(OUT, exist_ok=True)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)

    print("=== upload sources ===")
    sftp = c.open_sftp()
    for name in sorted(os.listdir(os.path.join(PROJ, "src"))):
        if name.endswith((".c", ".h")):
            sftp.put(os.path.join(PROJ, "src", name), "%s/src/%s" % (REMOTE, name))
    sftp.put(os.path.join(PROJ, "makefile"), REMOTE + "/makefile")
    sftp.close()
    print("[up] src/*.c,*.h + makefile")

    print("\n=== build on board ===")
    _i, o, e = c.exec_command(
        "cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1 | tail -4" % REMOTE, timeout=900)
    print(o.read().decode(errors="replace").rstrip())
    err = e.read().decode(errors="replace").rstrip()
    if err:
        print("[stderr]", err)

    print("\n=== restart service ===")
    _i, o, _e = c.exec_command(
        "systemctl restart tcp-image-server; sleep 2; systemctl is-active tcp-image-server", timeout=60)
    print(o.read().decode(errors="replace").rstrip())
    c.close()

    print("\n=== 分辨率扫描 (test.ppm, flags=proc,mask,overlay,stats) ===")
    for res in RES:
        args = [PY, os.path.join(UI, "imagelink.py"), HOST, "5060", "test.ppm",
                "--mode", "0", "--flags", "proc,mask,overlay,stats",
                "--ai-res", str(res),
                "--save-mask", os.path.join(OUT, "fullres_mask_%d.png" % res),
                "--save-overlay", os.path.join(OUT, "fullres_overlay_%d.png" % res),
                "--save-proc", os.path.join(OUT, "fullres_proc.png")]
        print("\n--- res=%d ---" % res)
        p = subprocess.run(args, cwd=UI, capture_output=True, text=True, timeout=600)
        for line in p.stdout.splitlines():
            if any(k in line for k in ("total=", "AI:", "summary", "LINK ERROR", "save:")):
                print(line)
        if p.stderr.strip():
            print("[stderr]", p.stderr.strip())

    print("\nSWEEP_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
