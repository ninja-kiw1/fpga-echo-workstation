"""Deploy the rebuilt boot.bin to the FZ3B's SD boot partition.

SAFETY:
  * before touching anything it pulls the CURRENT on-board boot.bin down to
    boot/orig_BOOT.BIN (PC-side backup -- the only recovery if the new image
    does not boot, since recovery needs a card reader),
  * also copies it to /run/media/mmcblk1p1/boot.bin.vendor on the card,
  * writes the new image to a temp name, verifies md5 there, then renames over
    boot.bin and syncs.

Usage:
  python deploy_bootbin.py                 # dry run: only backup + show sizes
  python deploy_bootbin.py --apply         # actually replace boot.bin
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
NEW = str(Path(__file__).resolve().parents[1] / "boot/parts/boot.bin")
BACKUP = str(Path(__file__).resolve().parents[1] / "boot/orig_BOOT.BIN")

apply = "--apply" in sys.argv


def md5(b):
    return hashlib.md5(b).hexdigest()


cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)


def run(cmd, timeout=120):
    _i, o, e = cli.exec_command(cmd, timeout=timeout)
    return (o.read().decode("utf-8", "replace").rstrip(),
            e.read().decode("utf-8", "replace").rstrip())


print("=== 1) back up the current on-board boot.bin ===")
sftp = cli.open_sftp()
cur = sftp.open(BOOTDIR + "/boot.bin", "rb").read()
open(BACKUP, "wb").write(cur)
print("  PC backup: %s  %d bytes  md5=%s" % (BACKUP, len(cur), md5(cur)))
sftp.putfo(open(BACKUP, "rb"), BOOTDIR + "/boot.bin.vendor")
print("  card copy: %s/boot.bin.vendor" % BOOTDIR)

newdata = open(NEW, "rb").read()
print("=== 2) new image ===")
print("  %s  %d bytes  md5=%s" % (NEW, len(newdata), md5(newdata)))
print("  growth: %+d bytes" % (len(newdata) - len(cur)))

o, _ = run("df -h %s | tail -1" % BOOTDIR)
print("  free space on boot partition: %s" % o)

sftp.close()

if not apply:
    print("\nDRY RUN -- nothing replaced. Re-run with --apply to flash.")
    cli.close()
    raise SystemExit(0)

print("\n=== 3) uploading new boot.bin (as boot.bin.new) ===")
sftp = cli.open_sftp()
sftp.put(NEW, BOOTDIR + "/boot.bin.new")
st = sftp.stat(BOOTDIR + "/boot.bin.new")
print("  uploaded %d bytes" % st.st_size)
sftp.close()

o, _ = run("md5sum %s/boot.bin.new" % BOOTDIR)
print("  card md5: %s" % o)
print("  expect  : %s" % md5(newdata))

print("\n=== 4) swap in ===")
o, e = run("cd %s && cp boot.bin boot.bin.old && mv -f boot.bin.new boot.bin && sync && md5sum boot.bin" % BOOTDIR)
print("  %s %s" % (o, e))
o, _ = run("ls -la %s" % BOOTDIR)
print(o)
cli.close()
print("\nDONE. Power-cycle the board to boot the new image.")
