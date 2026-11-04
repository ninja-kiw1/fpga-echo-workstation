# Dump CAMUS VAL frame pairs + myocardial block centres for the vis_layer block-matching C-sim (tb_bm.cpp).
# pairs.txt lines: name n file1 file2 K x0 y0 x1 y1 ...  (frames n x n uint8 raw)
import json, numpy as np
from scipy import ndimage as ndi
from skimage.morphology import skeletonize

C = 'E:/PLD/echo/data/cache/'
O = 'E:/PLD/strain/step1/data/'
import os; os.makedirs(O, exist_ok=True)
lines = []

def centres(myo, step, margin, n):
    sk = skeletonize(myo)
    ys, xs = np.nonzero(sk)
    pts = []
    for y, x in zip(ys, xs):
        if margin <= y < n - margin and margin <= x < n - margin and all(abs(y - py) + abs(x - px) >= step for py, px in pts):
            pts.append((y, x))
    return pts

# set 1: 512 ED frames, B = A shifted by a known integer (+3, -2) -> exact recovery expected (SAD 0)
img = np.load(C + 'val_img512.npy', mmap_mode='r'); lv = np.load(C + 'val_lv512.npy', mmap_mode='r')
epi = np.load(C + 'val_epi512.npy', mmap_mode='r')
for i in (0, 2, 10):
    a = np.ascontiguousarray(img[i]); b = np.roll(a, (-2, 3), axis=(0, 1))   # B(y, x) = A(y + 2, x - 3)
    myo = (epi[i] > 0) & ~(lv[i] > 0)
    pts = centres(myo, 10, 24, 512)[:40]
    a.tofile(O + f's1_{i}_a.raw'); b.tofile(O + f's1_{i}_b.raw')
    lines.append(f's1_{i} 512 s1_{i}_a.raw s1_{i}_b.raw {len(pts)} ' + ' '.join(f'{x} {y}' for y, x in pts))

# set 2: real motion, consecutive half-sequence frames (128), blocks on a band outside the LV mask
simg = np.load(C + 'seq_val_img128.npy', mmap_mode='r'); slv = np.load(C + 'seq_val_lv128.npy', mmap_mode='r')
idx = json.load(open(C + 'seq_val_index.json'))
for s in idx[:6]:
    t = s['start'] + s['length'] // 3
    a = np.ascontiguousarray(simg[t]); b = np.ascontiguousarray(simg[t + 1])
    m = slv[t] > 0
    band = ndi.binary_dilation(m, iterations=3) & ~m
    pts = centres(band, 6, 20, 128)[:40]
    nm = f's2_{t}'
    a.tofile(O + nm + '_a.raw'); b.tofile(O + nm + '_b.raw')
    lines.append(f'{nm} 128 {nm}_a.raw {nm}_b.raw {len(pts)} ' + ' '.join(f'{x} {y}' for y, x in pts))

open(O + 'pairs.txt', 'w').write('\n'.join(lines) + '\n')
print(len(lines), 'pairs,', sum(int(l.split()[4]) for l in lines), 'blocks')
