"""Sub-pixel bias of the SAD fit on CAMUS VAL frames: B = A shifted by a known fraction (bilinear), compare
parabola (bm_sad.c) vs equiangular (V) fit on the same integer SAD table (bm_sad_table_c)."""
import ctypes, sys, json
from pathlib import Path
import numpy as np
from scipy import ndimage
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import track_val as tv
lib = tv.lib
lib.bm_sad_table_c.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
meta = json.loads((tv.CACHE / 'val_meta.json').read_text())
seqs = sorted({(m['patient'], m['view']) for m in meta})[:10]
R = 3
def table(A, B, tx, ty):
    A = np.ascontiguousarray(A); B = np.ascontiguousarray(B)
    tab = np.zeros((2*R+1)**2, np.uint32)
    lib.bm_sad_table_c(A[ty:, tx:].ctypes.data, A.shape[1], B[ty:, tx:].ctypes.data, B.shape[1], R, tab.ctypes.data)
    return tab.reshape(2*R+1, 2*R+1).astype(float)
def parab(l, c, r):
    d = l + r - 2*c
    return 0.0 if d <= 0 else (l - r) / (2*d)
def vfit(l, c, r):
    d = max(l, r) - c
    return 0.0 if d <= 0 else (l - r) / (2*d)
for tier in (256, 512):
    res = {}
    for s in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7):
        ep, ev = [], []
        for p, v in seqs:
            frames, *_ = tv.load_sequence(p, v, tier)
            A = frames[len(frames)//2]
            B = np.clip(np.round(ndimage.shift(A.astype(float), (0, s), order=1)), 0, 255).astype(np.uint8)
            H, W = A.shape
            for ty in range(H//4, 3*H//4, H//16):
                for tx in range(W//4, 3*W//4, W//16):
                    t = table(A, B, tx, ty)
                    iy, ix = np.unravel_index(np.argmin(t), t.shape)
                    if not (0 < ix < 2*R): continue
                    dx = ix - R
                    ep.append(dx + parab(t[iy, ix-1], t[iy, ix], t[iy, ix+1]) - s)
                    ev.append(dx + vfit(t[iy, ix-1], t[iy, ix], t[iy, ix+1]) - s)
        res[s] = (np.mean(ep), np.mean(np.abs(ep)), np.mean(ev), np.mean(np.abs(ev)), len(ep))
        print(tier, s, 'parabola bias %.3f mae %.3f | V bias %.3f mae %.3f | n %d' % res[s], flush=True)
