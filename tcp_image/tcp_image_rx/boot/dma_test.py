"""Minimal AXI-DMA round-trip test (simple mode) straight from /dev/mem.

PL currently holds our design:
  AXI DMA  @ 0xA0000000   (S_AXI_LITE)
  AXI GPIO @ 0xA0010000   (ch1 = mode reg)
  DMA master sees DDR identity-mapped at 0x00000000 [2G]

Input @ 0x40000000, output @ 0x40100000, 1 MiB each (512*512 * 4B/px).
All polling is bounded so a hung DMA cannot wedge the shell.
(No mmap.flush() -- /dev/mem mappings reject it with EINVAL.)
"""
import array
import mmap
import os
import struct
import sys
import time

DMA_BASE = 0xA0000000
GPIO_BASE = 0xA0010000
IN_ADDR = 0x40000000
OUT_ADDR = 0x40100000
BYTES = 1024 * 1024

fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
dma = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=DMA_BASE)
gpio = mmap.mmap(fd, 0x10000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=GPIO_BASE)
inb = mmap.mmap(fd, BYTES, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=IN_ADDR)
outb = mmap.mmap(fd, BYTES, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OUT_ADDR)


def rd(m, off):
    return struct.unpack_from("<I", m, off)[0]


def wr(m, off, v):
    struct.pack_into("<I", m, off, v & 0xFFFFFFFF)


def hx(b):
    return " ".join("%02x" % c for c in b)


(MM2S_DMACR, MM2S_DMASR, MM2S_SRCADDR, MM2S_LENGTH) = (0x00, 0x04, 0x18, 0x28)
(S2MM_DMACR, S2MM_DMASR, S2MM_DESTADDR, S2MM_LENGTH) = (0x30, 0x34, 0x48, 0x58)


def show(tag):
    print("  [%-16s] MM2S_DMASR=%08X S2MM_DMASR=%08X" % (
        tag, rd(dma, MM2S_DMASR), rd(dma, S2MM_DMASR)))


print("== version/ID ==")
print("  S2MM_DMASR initial = %08X" % rd(dma, S2MM_DMASR))
show("initial")

# soft reset
wr(dma, MM2S_DMACR, 0x4)
wr(dma, S2MM_DMACR, 0x4)
time.sleep(0.05)
show("after reset")

# gradient pattern in input, zeros in output
vals = array.array("I", ((i & 0xFFFFFF) for i in range(BYTES // 4)))
if sys.byteorder != "little":
    vals.byteswap()
inb[:] = vals.tobytes()
outb[:] = b"\x00" * BYTES
print("  in[0:8]=%s   out[0:8]=%s" % (hx(inb[:8]), hx(outb[:8])))

wr(gpio, 0x00, 0)
print("  gpio DATA=%08X" % rd(gpio, 0x00))

# arm S2MM first
wr(dma, S2MM_DMACR, 0x1)
show("S2MM RS=1")
wr(dma, S2MM_DESTADDR, OUT_ADDR)
wr(dma, S2MM_LENGTH, BYTES)
show("S2MM len set")
# then MM2S
wr(dma, MM2S_DMACR, 0x1)
show("MM2S RS=1")
wr(dma, MM2S_SRCADDR, IN_ADDR)
wr(dma, MM2S_LENGTH, BYTES)
show("MM2S len set")

for i in range(2000):
    mm = rd(dma, MM2S_DMASR)
    ss = rd(dma, S2MM_DMASR)
    if i % 200 == 0:
        print("  t=%4d MM2S=%08X S2MM=%08X" % (i, mm, ss))
    if (mm & 0x2) and (ss & 0x2):
        print("  -> both Idle at t=%d" % i)
        break
    time.sleep(0.005)

mm = rd(dma, MM2S_DMASR)
ss = rd(dma, S2MM_DMASR)
print("== final DMASR ==")
print("  MM2S=%08X  S2MM=%08X" % (mm, ss))
print("  MM2S_LENGTH now=%d  S2MM_LENGTH now=%d" % (rd(dma, MM2S_LENGTH), rd(dma, S2MM_LENGTH)))
print("  out[0:16] after=%s" % hx(outb[:16]))

same = diff = 0
first = []
for i in range(0, BYTES, 4):
    a = struct.unpack_from("<I", inb, i)[0]
    b = struct.unpack_from("<I", outb, i)[0]
    if a == b:
        same += 1
    else:
        if len(first) < 5:
            first.append((i // 4, a, b))
        diff += 1
print("  pixels same=%d diff=%d of %d" % (same, diff, BYTES // 4))
for p in first:
    print("   diff @px%d in=%08X out=%08X" % p)

for m in (inb, outb, dma, gpio):
    m.close()
os.close(fd)
print("DMA_TEST_DONE")
