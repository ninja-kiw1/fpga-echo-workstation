# -*- coding: utf-8 -*-
"""P6-2: 真机换权重演示。

  1) 用 vectors_small_alt 生成 unet_net_data.c (模型 B) -> 部署 -> 重启服务
  2) 用 test.ppm 跑一次, 存回掩膜
  3) 恢复模型 A -> 重新部署 -> 重启服务 (无论成败都会恢复)

用法: python run_model_swap.py
"""
import os
import subprocess
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
HERE = r"E:/fpga/my_project/tcp_image_rx/ai/net"
PROJ = r"E:/fpga/my_project/tcp_image_rx"
UI = r"E:/fpga/my_project/tcp_image_ui"
REMOTE = "/root/tcp_image_rx"
OUT = os.path.join(HERE, "e2e_out")
PY = r"E:/fpga/vivado/2025.2.1/Vivado/tps/win64/python-3.13.0/python.exe"
SRC = os.path.join(PROJ, "src")

VEC_A = os.path.join(HERE, "vectors_small")
VEC_B = os.path.join(HERE, "vectors_small_alt")


def gen_net(vec):
    r = subprocess.run([PY, os.path.join(HERE, "gen_unet_c.py"), vec, SRC],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout.rstrip())
    if r.returncode != 0:
        raise SystemExit("gen_unet_c failed: %s" % r.stderr)


def deploy(c):
    sftp = c.open_sftp()
    for name in sorted(os.listdir(SRC)):
        if name.endswith((".c", ".h")):
            sftp.put(os.path.join(SRC, name), "%s/src/%s" % (REMOTE, name))
    sftp.close()
    for cmd in ("cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1 | tail -3" % REMOTE,
                "systemctl restart tcp-image-server; sleep 2; systemctl is-active tcp-image-server"):
        _i, o, e = c.exec_command(cmd, timeout=600)
        print("   " + o.read().decode(errors="replace").strip())
        err = e.read().decode(errors="replace").strip()
        if err:
            print("   [stderr]", err)


def shoot(name, save):
    args = [PY, os.path.join(UI, "imagelink.py"), HOST, "5060", "test.ppm",
            "--mode", "0", "--flags", "proc,mask,stats", "--save-mask", save]
    p = subprocess.run(args, cwd=UI, capture_output=True, text=True, timeout=180)
    print(p.stdout.rstrip())
    if p.stderr.strip():
        print("[stderr]", p.stderr.rstrip())
    print("   -> %s" % save)


def main():
    os.makedirs(OUT, exist_ok=True)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    ok = False
    try:
        print("=== [1/3] 部署模型 B (final 层的 mult/offset 取负) ===")
        gen_net(VEC_B)
        deploy(c)
        print("\n=== [2/3] 用 test.ppm 看看识别结果 ===")
        shoot("B", os.path.join(OUT, "test_mask_modelB.png"))
        ok = True
    finally:
        print("\n=== [3/3] 恢复模型 A 并重新部署 ===")
        gen_net(VEC_A)
        try:
            deploy(c)
        except Exception as e:
            print("!! 恢复失败, 请手工重跑 gen_unet_c.py + make:", e)
        c.close()
    print("\nMODEL_SWAP_DONE ok=%s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
