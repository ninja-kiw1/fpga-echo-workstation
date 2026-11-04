"""Generic hex dumper: hexdump.py <file> [off_hex ...]  (defaults to a few offsets)."""
import sys

path = sys.argv[1]
data = open(path, "rb").read()
print("%s  size=%d (0x%X)" % (path, len(data), len(data)))

if len(sys.argv) > 2:
    offs = [int(x, 16) for x in sys.argv[2:]]
else:
    offs = [0x0, 0x20, 0x100, 0x800, 0x2800]

for off in offs:
    print("\n===== @0x%X =====" % off)
    end = min(off + 0x80, len(data))
    for a in range(off, end, 16):
        chunk = data[a:min(a + 16, end)]
        hx = " ".join("%02x" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print("  %08x  %-47s  %s" % (a, hx, asc))
