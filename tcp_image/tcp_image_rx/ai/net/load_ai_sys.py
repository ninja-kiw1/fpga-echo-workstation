# -*- coding: utf-8 -*-
"""P4-0: 把 ai_sys 比特流加载进 PL，并用"安全读"确认通路。

安全规矩（见 progress_and_handoff.md 9.6③）：**先读确认有从机响应，绝不盲写**。
  1) 读 0xA0000000 (img_proc_axil VERSION) —— 厂商 PL 与我们的 PL 在那里都有从机,
     读一定安全, 且能区分是谁: 我们的 = 0x494D5003。
  2) VERSION 对上之后, 才读 0xA0010000 (conv CTRL) / 0xA0020000 (elt CTRL)。

板上脚本由 sftp 上传后执行。
"""
import hashlib
import os
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
LOCAL_BIN = r"E:/fpga/my_project/tcp_image_rx/bitstream/ai_sys.bin"
REMOTE_BIN = "/lib/firmware/ai_sys.bin"

REMOTE = r'''
import mmap, os, struct, hashlib, sys

def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

print("[md5] %s" % md5("/lib/firmware/ai_sys.bin"))
print("[state-before] %s" % open("/sys/class/fpga_manager/fpga0/state").read().strip())

with open("/sys/class/fpga_manager/fpga0/firmware", "w") as f:
    f.write("ai_sys.bin\n")
print("[load] wrote ai_sys.bin")
print("[state-after] %s" % open("/sys/class/fpga_manager/fpga0/state").read().strip())

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)

def rd32(base, off=0):
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
    v = struct.unpack_from("<I", m, off)[0]
    m.close()
    return v

ver = rd32(0xA0000000, 0x10)
print("[read] img_proc VERSION @0xA0000010 = 0x%08X  (expect 0x494D5003)" % ver)
if ver != 0x494D5003:
    print("[abort] PL is not our ai_sys design; will not probe conv/elt.")
    sys.exit(7)

c = rd32(0xA0010000)
e = rd32(0xA0020000)
print("[read] conv CTRL @0xA0010000 = 0x%08X  ap_idle=%d" % (c, (c >> 2) & 1))
print("[read] elt  CTRL @0xA0020000 = 0x%08X  ap_idle=%d" % (e, (e >> 2) & 1))

# 张量 BRAM 窗口 0xB000_0000(256KB, A1 前为 128KB): 只读探测, 有从机就不会挂
bsel = rd32(0xB0000000)
print("[read] BRAM  @0xB0000000 = 0x%08X  (window alive)" % bsel)
print("[ok] ai_sys loaded and control path alive")
'''


def main():
    local_md5 = hashlib.md5(open(LOCAL_BIN, "rb").read()).hexdigest()
    print("[local] ai_sys.bin %d B md5=%s" % (os.path.getsize(LOCAL_BIN), local_md5))

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    sftp.put(LOCAL_BIN, REMOTE_BIN)
    with sftp.open("/root/tcp_image_rx/net_load.py", "w") as fh:
        fh.write(REMOTE)
    sftp.close()

    _i, o, e = c.exec_command(
        "cd /root/tcp_image_rx && timeout 30 python3 net_load.py 2>&1; echo RC=$?", timeout=60)
    print(o.read().decode(errors="replace").rstrip())
    se = e.read().decode(errors="replace").rstrip()
    if se:
        print("[stderr]", se)
    c.close()


if __name__ == "__main__":
    main()
