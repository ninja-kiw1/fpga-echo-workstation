# -*- coding: utf-8 -*-
"""BRAM 写路径诊断：写两个标记值，然后全片扫描看它们落在哪（或没落）。"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
BOARD = r'''
import mmap, os, struct
BRAM = 0xA0100000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x20000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BRAM)
def rd(o): return struct.unpack_from("<I", m, o)[0]
def wr(o, v): struct.pack_into("<I", m, o, v)

print("write 0xDEADBEEF @0x000 ; 0x12345678 @0x400 ; 0xCAFEBABE @0x800")
wr(0x000, 0xDEADBEEF)
wr(0x400, 0x12345678)
wr(0x800, 0xCAFEBABE)
print("read back: 0x000=0x%08X  0x400=0x%08X  0x800=0x%08X"
      % (rd(0x000), rd(0x400), rd(0x800)))

marks = {0xDEADBEEF: "DEADBEEF", 0x12345678: "12345678", 0xCAFEBABE: "CAFEBABE"}
hits = []
for o in range(0, 0x20000, 4):
    v = rd(o)
    if v in marks:
        hits.append((hex(o), marks[v]))
print("marker hits: %r" % (hits[:20],))
nz = [(hex(o), hex(rd(o))) for o in range(0, 0x2000, 4) if rd(o) != 0]
print("first 8KB nonzero: %r" % (nz[:20],))
'''
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, timeout=15,
          look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
with sftp.open("/root/tcp_image_rx/bram_scan.py", "w") as fh:
    fh.write(BOARD)
sftp.close()
_i, o, e = c.exec_command("cd /root/tcp_image_rx && timeout 25 python3 bram_scan.py 2>&1; echo RC=$?", timeout=40)
print(o.read().decode(errors="replace").rstrip())
se = e.read().decode(errors="replace").rstrip()
if se:
    print("[stderr]", se)
c.close()
print("BRAM_SCAN_DONE")
