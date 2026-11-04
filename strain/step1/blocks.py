# Part E step 1: myocardium size on CAMUS VAL (expert masks = what programs 17/18 + F1 predict) -> blocks per frame.
import json, numpy as np
from scipy import ndimage as ndi
from skimage.morphology import skeletonize

C = 'E:/PLD/echo/data/cache/'
lv = np.load(C + 'val_lv512.npy', mmap_mode='r'); epi = np.load(C + 'val_epi512.npy', mmap_mode='r')
meta = json.load(open(C + 'val_meta.json'))
rows = []
for i, m in enumerate(meta):
    myo = (epi[i] > 0) & ~(lv[i] > 0)
    myo = ndi.binary_opening(myo, iterations=1)
    sx = m['width'] * m['spacing_mm'][0] / 512.0; sy = m['height'] * m['spacing_mm'][1] / 512.0
    area_px = int(myo.sum())
    sk = skeletonize(myo)
    # keep the largest skeleton component (the horseshoe)
    lab, n = ndi.label(sk, structure=np.ones((3, 3)))
    if n == 0:
        continue
    big = lab == (np.bincount(lab.ravel())[1:].argmax() + 1)
    ys, xs = np.nonzero(big)
    L_px = big.sum()  # ~ length in px (8-connected, diagonal steps counted 1)
    # length in mm with anisotropic pixels: count skeleton steps
    d = 0.0
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        a = big[max(0, -dy):512 - max(0, dy), max(0, -dx):512 - max(0, dx)]
        b = big[max(0, dy):, max(0, dx):][:a.shape[0], :a.shape[1]] if dx >= 0 else big[dy:, :512 + dx][:a.shape[0], :a.shape[1]]
        d += (a & b).sum() * np.hypot(dy * sy, dx * sx)
    L_mm = d
    thick_mm = area_px * sx * sy / max(L_mm, 1e-6)
    rows.append(dict(i=i, view=m['view'], phase=m['phase'], sx=sx, sy=sy, area_mm2=area_px * sx * sy, L_mm=L_mm,
                     thick_mm=thick_mm, bbox=[int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())]))

L = np.array([r['L_mm'] for r in rows]); T = np.array([r['thick_mm'] for r in rows])
SX = np.array([r['sx'] for r in rows]); SY = np.array([r['sy'] for r in rows])
print('frames', len(rows))
print('mid-wall length mm  P5/P50/P95 %.0f / %.0f / %.0f' % tuple(np.percentile(L, [5, 50, 95])))
print('wall thickness mm   P5/P50/P95 %.1f / %.1f / %.1f' % tuple(np.percentile(T, [5, 50, 95])))
print('px at 512 (mm) x %.3f y %.3f (median); at 256 x2' % (np.median(SX), np.median(SY)))
for sp in (2.0, 3.0, 4.0):
    k = np.ceil(L / sp)
    print('spacing %.0f mm: points per line P50 %.0f P95 %.0f  -> 3 lines (endo/mid/epi) P50 %.0f P95 %.0f'
          % (sp, np.median(k), np.percentile(k, 95), 3 * np.median(k), 3 * np.percentile(k, 95)))

# inter-frame motion from the half-sequence LV masks (128): boundary displacement lower bound
lvs = np.load(C + 'seq_val_lv128.npy', mmap_mode='r'); idx = json.load(open(C + 'seq_val_index.json'))
p95 = []; mx = []; lens = []
for s in idx:
    lens.append(s['length'])
    for t in range(s['start'], s['start'] + s['length'] - 1):
        a = lvs[t] > 0; b = lvs[t + 1] > 0
        ea = a ^ ndi.binary_erosion(a); eb = b ^ ndi.binary_erosion(b)
        if ea.sum() == 0 or eb.sum() == 0:
            continue
        da = ndi.distance_transform_edt(~ea)
        v = da[eb]
        p95.append(np.percentile(v, 95)); mx.append(v.max())
p95 = np.array(p95); mx = np.array(mx)
print('half sequences', len(idx), 'frames P50 %d (min %d max %d)' % (np.median(lens), min(lens), max(lens)))
print('LV boundary move per frame at 128 px: P95-of-frame median %.2f, P99 over pairs %.2f, max %.2f'
      % (np.median(p95), np.percentile(p95, 99), mx.max()))
json.dump(dict(L=L.tolist(), T=T.tolist()), open('E:/PLD/strain/step1/blocks.json', 'w'))
