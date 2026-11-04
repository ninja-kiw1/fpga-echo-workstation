"""Hex-dump regions of the vendor boot.bin so we can identify the ZynqMP boot
image layout (boot header -> image header table -> image headers -> partitions).

Offsets were read straight off the vendor boot header's candidate pointer fields.
"""
from pathlib import Path
import sys

BIN = str(Path(__file__).resolve().parents[1] / "boot/boot.bin")


def dump(data, off, n, title):
    print("\n===== %s  @0x%X (%d bytes) =====" % (title, off, n))
    end = min(off + n, len(data))
    for a in range(off, end, 16):
        chunk = data[a:min(a + 16, end)]
        hx = " ".join("%02x" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print("  %08x  %-47s  %s" % (a, hx, asc))


data = open(BIN, "rb").read()
print("boot.bin size = %d (0x%X)" % (len(data), len(data)))

# boot header
dump(data, 0x0000, 0x120, "boot header")

# candidate pointers seen in the header
for off in (0x800, 0x2800, 0x191B8, 0x1FAE0):
    dump(data, off, 0x80, "candidate")

# tail
dump(data, len(data) - 0x80, 0x80, "tail")
