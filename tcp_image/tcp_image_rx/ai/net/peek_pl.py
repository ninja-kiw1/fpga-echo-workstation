# -*- coding: utf-8 -*-
"""P4-0b: 判定 PL 里到底是谁 + 验证 /dev/mem 读法本身没问题。

只读，不写。读 0xFF5E00C0(CRL_APB PL0_REF_CTRL) 作为"读法正确性"的对照。
"""
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"

REMOTE = r'''
import mmap, os, struct
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)

def rd(base, off=0, n=1):
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=base)
    vals = struct.unpack_from("<%dI" % n, m, off)
    m.close()
    return vals

print("[check] PL0_REF_CTRL @0xFF5E00C0 = 0x%08X (expect 0x01010802 for 101.376MHz)" % rd(0xFF5E0000, 0xC0)[0])
print("[check] sanity @0xFFFD0000 = 0x%08X" % rd(0xFFFD0000)[0])

print("--- 0xA0000000 region (our img_proc VERSION should be 0x494D5003 at +0x10) ---")
vals = rd(0xA0000000, 0, 12)
for i, v in enumerate(vals):
    print("   0xA00000%02X = 0x%08X" % (i * 4, v))

print("--- 0xA0010000 (our conv CTRL, expect ap_idle bit2=1 -> xxxx4) ---")
vals = rd(0xA0010000, 0, 6)
for i, v in enumerate(vals):
    print("   0xA00100%02X = 0x%08X" % (i * 4, v))
print("DONE")
'''


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    sftp = c.open_sftp()
    with sftp.open("/root/tcp_image_rx/net_peek.py", "w") as fh:
        fh.write(REMOTE)
    sftp.close()
    _i, o, e = c.exec_command("cd /root/tcp_image_rx && timeout 30 python3 net_peek.py 2>&1; echo RC=$?", timeout=60)
    print(o.read().decode(errors="replace").rstrip())
    se = e.read().decode(errors="replace").rstrip()
    if se:
        print("[stderr]", se)
    c.close()


if __name__ == "__main__":
    main()
