# -*- coding: utf-8 -*-
"""把任意一个 .bin 当新的 PL 比特流烧到板子上（带**安全闸**）。

用法:  python _board_load_bin.py <local.bin> [keep-service]

安全规矩（血的教训，2026-10-06 实测踩过）：
  1) **重编 PL 之前必须先让 conv/elt 两个 HLS 核 idle。** 如果它们还有 AXI 事务在飞
     （比如 tcp-image-server 正在跑一次推理），重编 PL 会把 PS 的 AXI 互联**挂死**，
     整块板 ping 不通、SSH 不通，只能断电或 JTAG 救。本脚本会读 CTRL 寄存器确认
     ap_idle=1 才动手，等不到就直接**中止、不烧**。
  2) 烧之前先停掉 tcp-image-server，避免它在我们编程期间又发一次任务。
  3) 烧完做安全读确认是我们的设计（0xA0000010 == 0x494D5003）。

  默认烧完**不**自动起服务（跑板端自检时服务在跑会抢 PL，会出假数据）；
  需要服务的（比如上位机 sweep）在外部 `_board_svc.py start`。
"""
import hashlib
import os
import sys

import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
REMOTE_BIN = "/lib/firmware/ai_sys.bin"

REMOTE = r'''
import mmap, os, struct, hashlib, sys, time, subprocess

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

def sh(cmd):
    # 板载 Python 是 3.6, 没有 subprocess.run(capture_output=...)(3.7+), 别用
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    out = p.communicate()[0]
    return out.decode(errors="replace").strip()

def rd32(fd, base, off=0):
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED,
                  mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
    v = struct.unpack_from("<I", m, off)[0]
    m.close()
    return v

print("[md5] %s" % md5("/lib/firmware/ai_sys.bin"))
print("[state-before] %s" % open("/sys/class/fpga_manager/fpga0/state").read().strip())

# --- 停服务，免得它在我们编程期间又发一次任务 ---
print("[svc] stop tcp-image-server -> %s" % sh("systemctl stop tcp-image-server 2>&1; echo rc=$?"))
time.sleep(0.5)

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)

# --- 安全闸：两个 HLS 核必须 idle（CTRL bit2）---
ok = False
for i in range(20):
    ver = rd32(fd, 0xA0000000, 0x10)
    if ver != 0x494D5003:
        print("[abort] PL is not our design (VERSION=0x%08X); refuse to program." % ver)
        sys.exit(7)
    c = rd32(fd, 0xA0010000)
    e = rd32(fd, 0xA0020000)
    if (c >> 2) & 1 and (e >> 2) & 1:
        print("[gate] conv/elt idle (CTRL 0x%08X / 0x%08X) after %d poll(s)" % (c, e, i + 1))
        ok = True
        break
    time.sleep(0.5)
if not ok:
    print("[abort] conv/elt still BUSY (CTRL 0x%08X / 0x%08X): programming the PL now "
          "would hang the PS AXI interconnect. Nothing was programmed." % (c, e))
    sys.exit(9)

# --- 到这里才允许编程 ---
with open("/sys/class/fpga_manager/fpga0/firmware", "w") as f:
    f.write("ai_sys.bin\n")
print("[load] wrote ai_sys.bin")
print("[state-after] %s" % open("/sys/class/fpga_manager/fpga0/state").read().strip())

ver = rd32(fd, 0xA0000000, 0x10)
print("[read] img_proc VERSION @0xA0000010 = 0x%08X  (expect 0x494D5003)" % ver)
if ver != 0x494D5003:
    print("[abort] after load VERSION mismatch -> not our design")
    sys.exit(7)
c = rd32(fd, 0xA0010000)
e = rd32(fd, 0xA0020000)
print("[read] conv CTRL @0xA0010000 = 0x%08X  ap_idle=%d" % (c, (c >> 2) & 1))
print("[read] elt  CTRL @0xA0020000 = 0x%08X  ap_idle=%d" % (e, (e >> 2) & 1))
print("[read] BRAM  @0xB0000000 = 0x%08X  (window alive)" % rd32(fd, 0xB0000000))
# 烧完再把板上文件 md5 打一遍, 让上位机做端到端确认(防止"以为烧了其实没烧")
print("[md5-after] %s" % md5("/lib/firmware/ai_sys.bin"))
print("[ok] bitstream loaded, both HLS cores idle")
'''


def main():
    local_bin = sys.argv[1]
    keep = len(sys.argv) > 2 and sys.argv[2] == "keep-service"
    local_md5 = hashlib.md5(open(local_bin, "rb").read()).hexdigest()
    print("[local] %s %d B md5=%s" % (local_bin, os.path.getsize(local_bin), local_md5))

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    sftp.put(local_bin, REMOTE_BIN)
    with sftp.open("/root/tcp_image_rx/net_load.py", "w") as fh:
        fh.write(REMOTE)
    sftp.close()

    _i, o, e = c.exec_command(
        "cd /root/tcp_image_rx && timeout 90 python3 net_load.py 2>&1; echo RC=$?",
        timeout=150)
    txt = o.read().decode(errors="replace").rstrip()
    print(txt)
    se = e.read().decode(errors="replace").rstrip()
    if se:
        print("[stderr]", se)

    # 端到端确认：板上文件的 md5 必须等于本地文件
    ok_md5 = ("[md5-after] " + local_md5) in txt
    ok_idle = "[ok] bitstream loaded" in txt
    if ok_md5 and ok_idle:
        print(">>> LOAD_OK (%s)" % local_md5)
    else:
        print(">>> LOAD_FAILED (板上的设计没有换成功, 后面测出来的数字无效!)")
    if not ok_md5:
        return 1

    if keep:
        _i, o, _e = c.exec_command("systemctl start tcp-image-server; sleep 1; "
                                   "systemctl is-active tcp-image-server", timeout=30)
        print("[svc] restart -> %s" % o.read().decode(errors="replace").strip())
    c.close()
    return 0 if "RC=0" in txt else 1


if __name__ == "__main__":
    sys.exit(main())
