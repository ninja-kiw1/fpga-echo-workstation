"""决定性测试 v2: 程序走真实 /dev/mem mmap 路径收图, 用 mmap 读回逐字节比对。
(dd/read() 读 /dev/mem 会 EFAULT, 但 mmap 可以 —— 所以读回也用 mmap)
"""
import base64
import os
import subprocess
import sys
import time

import paramiko

HOST = "192.168.1.254"
REMOTE = "/root/tcp_image_rx"
REPO = r"E:/fpga/my_project/tcp_image_rx"
OUT = r"C:/Users/moxie/AppData/Local/Claude-3p/local-agent-mode-sessions/3fac2eea/00000000/94f9857c/outputs"
DDR = "0x40000000"
PORT = 5080
LOG = "/tmp/rx_ddr2.log"
IMG_BYTES = 786432

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="root", password="root", timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=60):
    _in, out, _err = cli.exec_command(cmd, timeout=timeout)
    try:
        return out.read().decode(errors="replace").rstrip()
    except Exception:
        return "(read timeout)"


def fire(cmd):
    cli.exec_command(cmd)
    time.sleep(2)


# 板上读回脚本: 用 mmap 读 0x40000000 的前 786432 字节, base64 输出
READBACK = (
    "import mmap,os,base64,sys\n"
    "fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC)\n"
    "m=mmap.mmap(fd,%d,mmap.MAP_SHARED,mmap.PROT_READ,offset=%s)\n"
    "sys.stdout.write(base64.b64encode(m[0:%d]).decode())\n"
    "m.close();os.close(fd)\n" % (IMG_BYTES, DDR, IMG_BYTES)
)
sftp = cli.open_sftp()
with sftp.open("/tmp/readback.py", "w") as f:
    f.write(READBACK)
sftp.close()

print("=== cleanup + start server on :%d with -a %s ===" % (PORT, DDR))
run("pkill -9 -f tcp_image_serve 2>/dev/null; sleep 1; echo ok")
fire("cd %s && setsid nohup ./tcp_image_server -p %d -a %s --no-dma -n 1 "
     "> %s 2>&1 < /dev/null &" % (REMOTE, PORT, DDR, LOG))
print("listener: %s" % run("ss -lntp 2>/dev/null | grep ':%d ' || echo NONE" % PORT))

sent = os.path.join(OUT, "e2e_sent_bars.rgb")
cmd = [sys.executable, os.path.join(REPO, "script", "send_image.py"),
       "-H", HOST, "-P", str(PORT), "--pattern", "bars", "--dump", sent]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
print(r.stdout.strip())
if r.returncode != 0:
    print("send rc=%d stderr=%s" % (r.returncode, r.stderr.strip()))

time.sleep(4)
print("\n=== board log ===")
print(run("cat %s" % LOG, 30))

print("\n=== read back via mmap ===")
b64 = run("python3 /tmp/readback.py", 90)
try:
    got = base64.b64decode(b64.strip())
except Exception as e:
    got = b""
    print("decode error: %s" % e)

want = open(sent, "rb").read()
print("read back %d bytes; sent %d bytes" % (len(got), len(want)))
if got and got == want:
    print("\n*** BYTE-FOR-BYTE IDENTICAL ***  (%d bytes)" % len(got))
elif got:
    n = min(len(got), len(want))
    diffs = [i for i in range(n) if got[i] != want[i]]
    print("\n*** MISMATCH *** first diff at %s, total %d/%d"
          % (diffs[0] if diffs else None, len(diffs), n))
else:
    print("\n*** readback returned nothing ***")

cli.close()
print("\nE2E_DDR2_DONE")
