"""Find the byte ranges where two boot images differ (to locate the tables that
depend on partition count)."""
import sys

a = open(sys.argv[1], "rb").read()
b = open(sys.argv[2], "rb").read()
print("A=%s size=%d   B=%s size=%d" % (sys.argv[1], len(a), sys.argv[2], len(b)))

n = min(len(a), len(b))
runs = []
start = None
for i in range(n):
    if a[i] != b[i]:
        if start is None:
            start = i
    else:
        if start is not None:
            runs.append((start, i))
            start = None
if start is not None:
    runs.append((start, n))

# merge runs separated by < 16 bytes
merged = []
for r in runs:
    if merged and r[0] - merged[-1][1] < 16:
        merged[-1] = (merged[-1][0], r[1])
    else:
        merged.append(list(r))

print("differing regions (merged):")
for s, e in merged:
    ctx_a = a[s:min(s + 16, e)].hex(" ")
    print("  0x%06X - 0x%06X  (%d B)   A: %s" % (s, e, e - s, ctx_a))

print("\nfirst 40 raw differing offsets:")
c = 0
for i in range(n):
    if a[i] != b[i]:
        print("  0x%06X  a=%02x b=%02x" % (i, a[i], b[i]))
        c += 1
        if c >= 40:
            break
