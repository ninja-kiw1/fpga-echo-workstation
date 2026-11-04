# -*- coding: utf-8 -*-
"""P5-4: 部署新板端程序 + 从 PC 端跑端到端(各种 flags 组合)。

用法: python run_p5_e2e.py
产出: ai/net/e2e_out/*.png (回传的预处理图/掩膜/叠加图)
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
LOCAL_BIN = os.path.join(PROJ, "bitstream", "ai_sys.bin")


def ssh_exec(c, cmd, timeout=300):
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    return out, err


def load_pl(c):
    sftp = c.open_sftp()
    sftp.put(LOCAL_BIN, "/lib/firmware/ai_sys.bin")
    with sftp.open("/root/tcp_image_rx/net_load2.py", "w") as fh:
        fh.write(
            "import mmap,os,struct\n"
            "open('/sys/class/fpga_manager/fpga0/firmware','w').write('ai_sys.bin\\n')\n"
            "fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC)\n"
            "def r(b,o=0):\n"
            "    m=mmap.mmap(fd,0x1000,mmap.MAP_SHARED,mmap.PROT_READ|mmap.PROT_WRITE,offset=b)\n"
            "    v=struct.unpack_from('<I',m,o)[0]; m.close(); return v\n"
            "print('VERSION=0x%08X' % r(0xA0000000,0x10))\n"
            "print('conv ctrl=0x%08X elt ctrl=0x%08X' % (r(0xA0010000), r(0xA0020000)))\n")
    sftp.close()
    out, _ = ssh_exec(c, "cd /root/tcp_image_rx && python3 net_load2.py 2>&1", 60)
    print(out.rstrip())


def main():
    os.makedirs(OUT, exist_ok=True)
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)

    print("=== load PL ===")
    load_pl(c)

    print("\n=== upload sources ===")
    sftp = c.open_sftp()
    _i, o, _e = c.exec_command("mkdir -p %s/test" % REMOTE)
    o.read()
    for name in sorted(os.listdir(os.path.join(PROJ, "src"))):
        if name.endswith((".c", ".h")):
            sftp.put(os.path.join(PROJ, "src", name), "%s/src/%s" % (REMOTE, name))
    sftp.put(os.path.join(PROJ, "makefile"), REMOTE + "/makefile")
    sftp.put(os.path.join(PROJ, "test", "unet_selftest.c"), REMOTE + "/test/unet_selftest.c")
    sftp.close()
    print("[up] src/*.c,*.h + makefile + test/unet_selftest.c")

    print("\n=== build on board ===")
    out, err = ssh_exec(c, "cd %s && make clean >/dev/null 2>&1; make CC=gcc 2>&1 | tail -8; "
                           "make CC=gcc unet_selftest 2>&1 | tail -3" % REMOTE, 600)
    print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())

    print("\n=== restart service ===")
    out, _ = ssh_exec(c, "systemctl restart tcp-image-server; sleep 2; "
                         "systemctl is-active tcp-image-server; "
                         "journalctl -u tcp-image-server -n 20 --no-pager | tail -18", 60)
    print(out.rstrip())

    # ---- PC 端各种 flags 组合 ----
    combos = [
        ("baseline(v2 兼容头)", dict(mode=0, extra=["--v2"], save="proc_00_tcp_out.png")),
        ("proc",            dict(mode=0, extra=[], save="proc_00_tcp_out.png")),
        ("proc+stats",      dict(mode=0, extra=["--flags", "proc,stats"])),
        ("mask+stats",      dict(mode=0, extra=["--flags", "mask,stats"],
                                 save="ai_mask.png")),
        ("overlay+stats",   dict(mode=0, extra=["--flags", "overlay,stats"],
                                 save="ai_overlay.png")),
        ("overlay(ai=proc)", dict(mode=0, extra=["--flags", "overlay,stats",
                                                 "--ai-src", "proc"],
                                  save="ai_overlay_fromproc.png")),
        ("stats only",      dict(mode=0, extra=["--flags", "noimage,stats"])),
        ("mode3 pcolor+mask", dict(mode=3, extra=["--flags", "proc,mask,stats"],
                                   save="ai_mask_mode3.png")),
    ]
    rc = 0
    for title, cfg in combos:
        print("\n--- %s ---" % title)
        args = [PY, os.path.join(UI, "imagelink.py"), HOST, "5060", "bars",
                "--mode", str(cfg["mode"])] + cfg["extra"]
        if cfg.get("save"):
            args += ["--save", os.path.join(OUT, cfg["save"])]
        p = subprocess.run(args, cwd=UI, capture_output=True, text=True, timeout=180)
        print(p.stdout.rstrip())
        if p.stderr.strip():
            print("[stderr]", p.stderr.rstrip())
        if p.returncode != 0:
            rc = 1

    c.close()
    print("\nE2E_DONE rc=%d" % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
