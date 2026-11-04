# -*- coding: utf-8 -*-
"""P3: 单层卷积上板，与 PLD 的 expected.bin 逐字节比对。

用例 syn_rgb_3x3_int16: mode=0(3x3) cin=3 cout=8 in_h=7 in_w=9 relu=1 shift=27 qmin=-32767 qmax=32767
地址(第二轮 BD): conv 控制 0xA001_0000 / BRAM 0xA010_0000
寄存器偏移取自 IP 的 xconv_layer_hw.h。
"""
import os
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"
CASE = (r"E:/fpga/my_project/PLD_share_20260929/PLD/medical_fpga/deployment/"
        r"unet_conv/vectors/syn_rgb_3x3_int16")
REMOTE = "/root/tcp_image_rx"

BOARD = r'''
import mmap, os, struct, time
BRAM = 0xB0000000
CONV = 0xA0010000
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
bram = mmap.mmap(fd, 0x20000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=BRAM)
creg = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=CONV)
def crd(o): return struct.unpack_from("<I", creg, o)[0]
def cwr(o, v): struct.pack_into("<I", creg, o, v)

print("0) 先读确认 IP 在响应 (避免访问无从机地址把 CPU 挂死)")
ctrl = crd(0x00)
print("   conv CTRL=0x%08X  ap_idle(bit2)=%d" % (ctrl, (ctrl >> 2) & 1))
if (ctrl >> 2) & 1 == 0:
    print("   !! ap_idle 不是 1, 可能 IP 不对, 中止")
    raise SystemExit(1)

def rd(p):
    with open(p, "rb") as f:
        return f.read()

params = rd("/root/tcp_image_rx/p3_params.bin")
inp    = rd("/root/tcp_image_rx/p3_input.bin")
exp    = rd("/root/tcp_image_rx/p3_expected.bin")
print("1) params=%d B  input=%d B  expected=%d B" % (len(params), len(inp), len(exp)))

P_OFF, I_OFF, O_OFF = 0x0, 0x400, 0x800
bram[P_OFF:P_OFF + len(params)] = params
bram[I_OFF:I_OFF + len(inp)] = inp

print("2) 写配置寄存器")
cwr(0x30, (BRAM + P_OFF) & 0xFFFFFFFF); cwr(0x34, (BRAM + P_OFF) >> 32)
cwr(0x18, (BRAM + I_OFF) & 0xFFFFFFFF); cwr(0x1C, (BRAM + I_OFF) >> 32)
cwr(0x24, (BRAM + O_OFF) & 0xFFFFFFFF); cwr(0x28, (BRAM + O_OFF) >> 32)
cwr(0x3c, 0)          # mode 0 = 3x3
cwr(0x44, 3)          # cin
cwr(0x4c, 8)          # cout
cwr(0x54, 7)          # in_h
cwr(0x5c, 9)          # in_w
cwr(0x64, 1)          # relu
cwr(0x6c, 27)         # shift
cwr(0x74, (-32767) & 0xFFFFFFFF)
cwr(0x7c, 32767)
print("   回读 mode=%d cin=%d cout=%d in_h=%d in_w=%d relu=%d shift=%d" % (
    crd(0x3c), crd(0x44), crd(0x4c), crd(0x54), crd(0x5c), crd(0x64), crd(0x6c)))

print("3) 启动 ap_start, 轮询 ap_done")
cwr(0x00, 1)
t0 = time.time()
st = 0
while time.time() - t0 < 5.0:
    st = crd(0x00)
    if st & 2:
        break
elapsed = time.time() - t0
print("   CTRL=0x%08X  ap_done=%d  %.1f ms   ap_return=%d" % (st, (st >> 1) & 1, elapsed * 1000, crd(0x10)))

out = bytes(bram[O_OFF:O_OFF + len(exp)])
bad = sum(1 for a, b in zip(out, exp) if a != b)
print("4) 输出比对: mismatch = %d / %d 字节  ->  %s" % (bad, len(exp), "PASS" if bad == 0 else "FAIL"))
if bad:
    for i in range(len(exp)):
        if out[i] != exp[i]:
            print("   first diff at byte %d: got 0x%02X exp 0x%02X" % (i, out[i], exp[i]))
            break
print("P3_DONE")
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PW, timeout=15,
          look_for_keys=False, allow_agent=False)
sftp = c.open_sftp()
for local, remote in ((CASE + "/params.bin", REMOTE + "/p3_params.bin"),
                      (CASE + "/input.bin", REMOTE + "/p3_input.bin"),
                      (CASE + "/expected.bin", REMOTE + "/p3_expected.bin")):
    sftp.put(local, remote)
    print("put", os.path.basename(remote), os.path.getsize(local), "B")
with sftp.open(REMOTE + "/p3_layer.py", "w") as fh:
    fh.write(BOARD)
sftp.close()

_i, o, e = c.exec_command("cd " + REMOTE + " && timeout 30 python3 p3_layer.py 2>&1; echo RC=$?", timeout=45)
print(o.read().decode(errors="replace").rstrip())
se = e.read().decode(errors="replace").rstrip()
if se:
    print("[stderr]", se)
c.close()
print("P3_DRIVER_DONE")
