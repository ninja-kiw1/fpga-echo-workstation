"""Filtered tail of a Vivado log: drops the echoed-script lines (start with '#')."""
import sys

path = sys.argv[1]
n = int(sys.argv[2]) if len(sys.argv) > 2 else 40
try:
    data = open(path, "r", errors="replace").read()
except Exception as e:
    print("ERR", e)
    sys.exit(1)

keep = []
for l in data.split("\n"):
    s = l.lstrip()
    if s.startswith("#") or s.startswith("###"):
        continue
    if s.strip() == "":
        continue
    keep.append(l)

print("total_lines=%d  kept=%d" % (len(data.split("\n")), len(keep)))
print("\n".join(keep[-n:]))
