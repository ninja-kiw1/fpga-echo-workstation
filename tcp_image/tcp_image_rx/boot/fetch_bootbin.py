"""List the SD boot partition and pull boot.bin (+ image.ub header info) to the PC.

Run with Vivado's bundled python (paramiko in --user).
"""
from pathlib import Path
import hashlib
import os
import sys

import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"
BOOTDIR = "/run/media/mmcblk1p1"
LOCAL = str(Path(__file__).resolve().parents[1] / "boot")

os.makedirs(LOCAL, exist_ok=True)

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=60):
    _in, out, err = cli.exec_command(cmd, timeout=timeout)
    return (out.read().decode("utf-8", errors="replace").rstrip(),
            err.read().decode("utf-8", errors="replace").rstrip())


print("=== boot partition listing ===")
o, e = run("ls -la %s" % BOOTDIR)
print(o)
if e:
    print("[stderr]", e)

print("\n=== file types ===")
o, _ = run("for f in %s/*; do echo -n \"$f : \"; ls -l \"$f\" | awk '{print $5}'; done" % BOOTDIR)
print(o)

print("\n=== boot.bin head (first 64 bytes hex) ===")
o, _ = run("od -A x -t x1z %s/boot.bin | head -8" % BOOTDIR)
print(o)

print("\n=== pull boot.bin ===")
sftp = cli.open_sftp()
for name in ("boot.bin",):
    remote = "%s/%s" % (BOOTDIR, name)
    local = os.path.join(LOCAL, name)
    st = sftp.stat(remote)
    sftp.get(remote, local)
    data = open(local, "rb").read()
    print("  %s  size=%d  md5=%s" % (name, len(data), hashlib.md5(data).hexdigest()))

print("\n=== remote md5 (verify) ===")
o, _ = run("md5sum %s/boot.bin" % BOOTDIR)
print(o)

sftp.close()
cli.close()
print("\nFETCH_DONE")
