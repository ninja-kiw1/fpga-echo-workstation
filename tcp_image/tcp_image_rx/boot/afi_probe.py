"""Probe whether loading OUR bitstream changes the AFI (PS-PL) port registers.

The board currently runs the vendor PL.  We read AFI_FM0..FM5 word0, load our
DMA bitstream through fpga_manager, and read again.  If the values change, the
AFI config is tied to the PL-load path (and a fresh bitstream load is what
broke PL->PS in the earlier session).
"""
from pathlib import Path
import mmap
import os
import struct
import time

import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"
LOCAL_BIN = str(Path(__file__).resolve().parents[1] / "bitstream/system_wrapper.bin")
REMOTE_BIN = "/lib/firmware/system_wrapper.bin"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=60):
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", errors="replace").rstrip(),
            err.read().decode("utf-8", errors="replace").rstrip())


print("=== state before ===")
print(run("cat /sys/class/fpga_manager/fpga0/state; lsmod | grep -i fpga || echo '(fpgadrv not loaded)'")[0])

# upload our bitstream
sftp = cli.open_sftp()
st = sftp.stat(LOCAL_BIN) if False else None
sftp.put(LOCAL_BIN, REMOTE_BIN)
print("uploaded %s" % REMOTE_BIN)
sftp.close()

AFI_READ = (
    "import mmap,os,struct\n"
    "fd=os.open('/dev/mem',os.O_RDWR|os.O_SYNC)\n"
    "out=[]\n"
    "for n in range(6):\n"
    "    m=mmap.mmap(fd,0x1000,mmap.MAP_SHARED,mmap.PROT_READ,offset=0xFD360000+n*0x10000)\n"
    "    out.append('FM%d=%08X'%(n,struct.unpack_from('<I',m,0)[0]))\n"
    "    m.close()\n"
    "m=mmap.mmap(fd,0x1000,mmap.MAP_SHARED,mmap.PROT_READ,offset=0xFF9B0000)\n"
    "out.append('FM6=%08X'%struct.unpack_from('<I',m,0)[0])\n"
    "m.close();os.close(fd)\n"
    "print(' '.join(out))\n"
)
sftp = cli.open_sftp()
with sftp.open("/tmp/afi_words.py", "w") as f:
    f.write(AFI_READ)
sftp.close()

print("\n=== AFI before ===")
print(run("python3 /tmp/afi_words.py")[0])

print("\n=== load our bitstream ===")
print("rmmod:", run("rmmod fpgadrv 2>&1 || echo '(rmmod failed/none)'")[0])
o, e = run("echo %s > /sys/class/fpga_manager/fpga0/firmware 2>&1; echo rc=$?" % os.path.basename(REMOTE_BIN))
print("load:", o, e)
time.sleep(1)
print("state after:", run("cat /sys/class/fpga_manager/fpga0/state")[0])

print("\n=== AFI after ===")
print(run("python3 /tmp/afi_words.py")[0])

cli.close()
print("\nAFI_PROBE_DONE")
