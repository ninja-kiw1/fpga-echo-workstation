"""Read PS-PL interface control registers: AFI_FS (interface width), AFI resets,
and the SLCR blocks around them."""
import mmap
import os
import struct

BLOCKS = [
    ("FPD_SLCR.AFI_FS @0xFD615000", 0xFD615000, 0x40),
    ("LPD_SLCR.AFI_FS @0xFF419000", 0xFF419000, 0x40),
    ("CRF_APB.RST_FPD_TOP @0xFD1A0100", 0xFD1A0100, 0x40),
    ("CRL_APB.RST_LPD_TOP @0xFF5E023C", 0xFF5E023C, 0x20),
    ("CRL_APB.AFI0_REF @0xFF5E00C0", 0xFF5E00C0, 0x60),
    ("LPD_SLCR @0xFF410000", 0xFF410000, 0x40),
    ("FPD_SLCR @0xFD610000", 0xFD610000, 0x40),
]

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
for name, base, ln in BLOCKS:
    m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=base)
    parts = []
    for off in range(0, ln, 4):
        parts.append("+%03X=%08X" % (off, struct.unpack_from("<I", m, off)[0]))
    print("%s" % name)
    for i in range(0, len(parts), 4):
        print("   " + "  ".join(parts[i:i + 4]))
    m.close()
os.close(fd)
print("PSPL_READ_DONE")
