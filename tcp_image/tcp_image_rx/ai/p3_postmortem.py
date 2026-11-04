# -*- coding: utf-8 -*-
"""P3 事后分析：把 BRAM 三个区域读回来，和上传的文件逐字节对比，定位数据落在哪一步丢了。"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
BOARD = r'''
import mmap, os, struct
BRAM = 0xA0100000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x20000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BRAM)
def rd(p):
    with open(p, "rb") as f:
        return f.read()
for name, off, path in (("params", 0x000, "/root/tcp_image_rx/p3_params.bin"),
                        ("input ", 0x400, "/root/tcp_image_rx/p3_input.bin"),
                        ("output", 0x800, "/root/tcp_image_rx/p3_expected.bin")):
    want = rd(path)
    got = bytes(m[off:off + len(want)])
    bad = sum(1 for a, b in zip(got, want) if a != b)
    nz = sum(1 for b in got if b != 0)
    print("%s: len=%d  mismatch=%d/%d  非零字节=%d" % (name, len(want), bad, len(want), nz))
    print("   got[:16] = %s" % got[:16].hex())
    print("   want[:16]= %s" % want[:16].hex())
'''
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, timeout=15,
          look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
with sftp.open("/root/tcp_image_rx/p3_post.py", "w") as fh:
    fh.write(BOARD)
sftp.close()
_i, o, e = c.exec_command("cd /root/tcp_image_rx && timeout 25 python3 p3_post.py 2>&1; echo RC=$?", timeout=40)
print(o.read().decode(errors="replace").rstrip())
se = e.read().decode(errors="replace").rstrip()
if se:
    print("[stderr]", se)
c.close()
print("P3_POST_DONE")
