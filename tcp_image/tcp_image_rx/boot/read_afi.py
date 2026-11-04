"""Dump the ZynqMP AFI (Fabric Manager) register blocks from /dev/mem.

FM0..FM5 live at 0xFD360000 + n*0x10000 (one per PL->PS slave port candidate),
FM6 (LPD) at 0xFF9B0000.  Used to see whether ANY port is configured, and to
test whether poking them enables a port.
"""
import mmap
import os
import struct

BASES = [(0xFD360000 + n * 0x10000) for n in range(6)] + [0xFF9B0000]
OFFS = [0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C,
        0x30, 0x34, 0x38, 0x3C, 0x40, 0x44, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x5C, 0x60]

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
for base in BASES:
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                  offset=base)
    vals = []
    for off in OFFS:
        v = struct.unpack_from("<I", m, off)[0]
        vals.append("+%02X=%08X" % (off, v))
    print("AFI @ 0x%08X:" % base)
    for i in range(0, len(vals), 6):
        print("   " + "  ".join(vals[i:i + 6]))
    m.close()
os.close(fd)
print("AFI_DUMP_DONE")
