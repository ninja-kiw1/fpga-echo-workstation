"""Extract ATF (bl31) and U-Boot from the vendor boot.bin and wrap each as a
minimal ELF64 so bootgen can re-embed them.

Geometry comes from `bootgen -arch zynqmp -read boot.bin`:
  bl31.elf    : byte off 0x58AD80  len 12936   load/exec 0xFFFEA000
  u-boot.elf  : byte off 0x5977C0  len 196734  load/exec 0x08000000
(partition_offset in the header is in 32-bit WORDS; total_length x4 = bytes.)
"""
from pathlib import Path
import os
import struct

SRC = str(Path(__file__).resolve().parents[1] / "boot/boot.bin")
OUT = str(Path(__file__).resolve().parents[1] / "boot/parts")

PARTS = [
    ("bl31", 0x58AD80, 12936, 0xFFFEA000),
    ("u-boot", 0x5977C0, 196734, 0x08000000),
]

os.makedirs(OUT, exist_ok=True)
data = open(SRC, "rb").read()

for name, off, ln, load in PARTS:
    blob = data[off:off + ln]
    assert len(blob) == ln, "%s: short read" % name
    rawpath = os.path.join(OUT, name + ".bin")
    open(rawpath, "wb").write(blob)
    print("%-8s raw  %-40s %6d bytes  first=%s" % (name, rawpath, len(blob), blob[:8].hex()))

    # minimal ELF64 (little-endian, AArch64, ET_EXEC, one PT_LOAD)
    EH = 64
    PH = 56
    DATA_OFF = 0x1000
    ehdr = bytearray()
    ehdr += b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8   # ident
    ehdr += struct.pack("<HHI", 2, 183, 1)                    # type, machine, version
    ehdr += struct.pack("<QQQ", load, EH, 0)                  # entry, phoff, shoff
    ehdr += struct.pack("<IHHHHHH", 0, EH, PH, 1, 64, 0, 0)   # flags,ehsize,phentsize,phnum,...
    assert len(ehdr) == EH

    phdr = struct.pack("<IIQQQQQQ", 1, 5, DATA_OFF, load, load, len(blob), len(blob), 0x1000)
    assert len(phdr) == PH

    elf = bytes(ehdr) + phdr
    elf += b"\x00" * (DATA_OFF - len(elf))
    elf += blob
    elfpath = os.path.join(OUT, name + ".elf")
    open(elfpath, "wb").write(elf)
    print("%-8s elf  %-40s %6d bytes  entry=0x%X load=0x%X" % (name, elfpath, len(elf), load, load))

print("EXTRACT_DONE")
