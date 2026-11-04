"""Extract a zip into a new directory, refusing absolute paths and '..' members."""
import sys, zipfile, os
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=False)
root = os.path.realpath(dst)
with zipfile.ZipFile(src) as z:
    for m in z.infolist():
        p = os.path.realpath(os.path.join(dst, m.filename))
        if not p.startswith(root + os.sep) and p != root:
            raise SystemExit('unsafe member %r' % m.filename)
    z.extractall(dst)
print('extracted', len(z.infolist()), 'members to', dst)
