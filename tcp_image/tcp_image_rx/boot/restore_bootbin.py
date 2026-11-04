"""Restore the vendor boot.bin onto the SD card's boot partition specified on the command line.

Verifies sizes + md5 before and after the copy.
"""
import argparse
from pathlib import Path
import hashlib
import os
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("boot_partition", type=Path, help="Explicit SD-card boot partition root")
DEV = str(parser.parse_args().boot_partition.resolve()) + "/"
SRC = DEV + "boot.bin.vendor"
DST = DEV + "boot.bin"
VENDOR_MD5 = "cc316f785c3b40f46caef59e569bc988"
VENDOR_SIZE = 6650296


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


print("=== before ===")
for name in ("boot.bin", "boot.bin.vendor", "boot.bin.old", "image.ub"):
    p = DEV + name
    if os.path.exists(p):
        print("  %-18s %9d  %s" % (name, os.path.getsize(p), md5(p)))
    else:
        print("  %-18s MISSING" % name)

if not os.path.exists(SRC):
    raise SystemExit("boot.bin.vendor not found - aborting")

if md5(SRC) != VENDOR_MD5:
    raise SystemExit("boot.bin.vendor md5 does not match the expected vendor image - aborting")

print("\n=== restoring boot.bin.vendor -> boot.bin ===")
shutil.copyfile(SRC, DST)
print("copied %d bytes" % os.path.getsize(DST))

print("\n=== after ===")
for name in ("boot.bin", "image.ub"):
    p = DEV + name
    print("  %-18s %9d  %s" % (name, os.path.getsize(p), md5(p)))

ok = (os.path.getsize(DST) == VENDOR_SIZE and md5(DST) == VENDOR_MD5)
print("\nRESTORE_OK=%s" % ok)
