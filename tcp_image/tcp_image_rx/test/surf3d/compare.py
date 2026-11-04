"""Host check of src/surf3d.c (P1 1.4) against tcp_image_ui/ct_volume.Surface / render (float, by tolerance).

Volumes: MSD spleen labels (radiological view, the masks ctwin renders) of the 7 VAL + 4 train volumes used in
the board acceptance (test split not touched), plus synthetic shapes (ellipsoid, two blobs, thin plate,
one slice). For each: smoothed-volume size, voxels > 0.5, surface points, ext, and the rendered images at
6 angles: pixels that differ, mean / max absolute difference, and the foreground masks.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe compare.py [--board]   (gcc via WSL)
"""
import json
import os
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
PLD = os.path.normpath(os.path.join(RX, '..', '..'))
sys.path.insert(0, os.path.join(RX, '..', 'tcp_image_ui'))
import ct_volume as cv  # noqa: E402

ANGLES = [0.0, 30.0, 90.0, 137.5, 210.0, 300.0]
SIZE = 320
WORK = os.path.join(HERE, 'work')
CASES = ['spleen_10', 'spleen_25', 'spleen_41', 'spleen_47', 'spleen_52', 'spleen_56', 'spleen_59',
         'spleen_12', 'spleen_16', 'spleen_53', 'spleen_27']


def wsl(p):
    p = os.path.abspath(p).replace('\\', '/')
    return '/mnt/' + p[0].lower() + p[2:]


def synthetic():
    out = []
    z, y, x = np.mgrid[0:40, 0:512, 0:512]
    e = ((z - 20) / 15.0) ** 2 + ((y - 250) / 60.0) ** 2 + ((x - 300) / 40.0) ** 2 <= 1
    out.append(('ellipsoid', e, (0.8, 0.8, 5.0)))
    b = (((z - 10) / 6.0) ** 2 + ((y - 200) / 30.0) ** 2 + ((x - 200) / 30.0) ** 2 <= 1) | \
        (((z - 28) / 8.0) ** 2 + ((y - 320) / 50.0) ** 2 + ((x - 330) / 25.0) ** 2 <= 1)
    out.append(('two_blobs', b, (0.7, 0.7, 2.5)))
    p = np.zeros_like(e)
    p[18:21, 100:400, 150:170] = True
    out.append(('thin_plate', p, (0.9, 0.9, 1.0)))
    one = np.zeros((5, 512, 512), bool)
    one[2, 240:280, 240:300] = True
    out.append(('one_slice', one, (0.75, 0.75, 3.0)))
    return out


def main():
    os.makedirs(WORK, exist_ok=True)
    exe = os.path.join(WORK, 'harness_surf')
    subprocess.check_call(['wsl', 'gcc', '-O2', '-std=c99', '-D_GNU_SOURCE', '-o', wsl(exe), wsl(os.path.join(HERE, 'harness_surf.c')),
                           '-lm'])
    vols = []
    for c in CASES:
        v = cv.load_ct(os.path.join(PLD, 'Task09_Spleen', 'Task09_Spleen', 'imagesTr', c + '.nii.gz'))
        vols.append((c, v['label'], v['spacing']))
    vols += synthetic()
    rows = []
    for name, m, sp in vols:
        m = np.ascontiguousarray(m, np.uint8)
        t0 = time.perf_counter()
        s = cv.Surface(m.astype(bool), sp)
        t_build = time.perf_counter() - t0
        t0 = time.perf_counter()
        ref = [s.render(a, SIZE) for a in ANGLES]
        t_render = (time.perf_counter() - t0) / len(ANGLES)
        cf = os.path.join(WORK, name + '.bin')
        with open(cf, 'wb') as f:
            f.write(struct.pack('<6i', m.shape[0], m.shape[1], m.shape[2], 2, SIZE, len(ANGLES)))
            f.write(struct.pack('<3d', *[float(x) for x in sp]) + struct.pack('<%dd' % len(ANGLES), *ANGLES))
            f.write(m.tobytes())
        pre = os.path.join(WORK, name)
        subprocess.check_call(['wsl', wsl(exe), wsl(cf), wsl(pre)], stderr=subprocess.DEVNULL)
        st = json.load(open(pre + '.json'))
        row = dict(name=name, empty=bool(st['empty']), py_empty=bool(s.empty), c_build_ms=st['build_us'] / 1000.0,
                   py_build_ms=t_build * 1000.0, py_render_ms=t_render * 1000.0)
        if not s.empty:
            from scipy import ndimage
            # the Python smoothed volume (same steps as Surface.__init__) for the counts
            mm = m.astype(bool)[:, ::2, ::2]
            zz, rr, cc = np.nonzero(mm)
            mm = mm[max(zz.min() - 1, 0):zz.max() + 2, max(rr.min() - 3, 0):rr.max() + 4, max(cc.min() - 3, 0):cc.max() + 4]
            zf = max(1.0, sp[2] / (sp[0] * 2))
            g = ndimage.gaussian_filter(ndimage.zoom(mm.astype(np.float32), (zf, 1, 1), order=1), 1.2)
            row.update(py_dims=list(g.shape), c_dims=st['dims'], py_inside=int((g > 0.5).sum()), c_inside=st['inside'],
                       py_points=int(len(s.p[0])), c_points=st['n'], py_ext=s.ext, c_ext=st['ext'])
        diffs = []
        for i, r in enumerate(ref):
            c = np.fromfile(pre + '_%d.rgb' % i, np.uint8).reshape(SIZE, SIZE, 3)
            d = np.abs(c.astype(np.int64) - r.astype(np.int64))
            fg_r, fg_c = np.any(r != 24, -1), np.any(c != 24, -1)
            diffs.append(dict(angle=ANGLES[i], px_differ=int(np.any(d > 0, -1).sum()), px_differ_gt2=int(np.any(d > 2, -1).sum()),
                              mean_abs=float(d.mean()), max_abs=int(d.max()), fg_py=int(fg_r.sum()),
                              fg_xor=int((fg_r ^ fg_c).sum())))
        row['renders'] = diffs
        rows.append(row)
        print(name, {k: v for k, v in row.items() if k != 'renders'},
              'worst angle: px_differ %d (>2: %d) max %d mean %.4f fg_xor %d' % max(
                  ((x['px_differ'], x['px_differ_gt2'], x['max_abs'], x['mean_abs'], x['fg_xor']) for x in diffs)), flush=True)
    ok = all(r['empty'] == r['py_empty'] for r in rows) and all(
        r.get('py_points') == r.get('c_points') and r.get('py_inside') == r.get('c_inside') and r.get('py_dims') == r.get('c_dims')
        for r in rows if not r['py_empty'])
    tot = sum(x['fg_py'] for r in rows for x in r['renders'])
    dif = sum(x['px_differ_gt2'] for r in rows for x in r['renders'])
    rep = dict(cases=rows, counts_identical=ok, angles=ANGLES, size=SIZE,
               fg_pixels=tot, pixels_diff_gt2=dif, max_abs=max(x['max_abs'] for r in rows for x in r['renders']),
               mean_abs=float(np.mean([x['mean_abs'] for r in rows for x in r['renders']])),
               fg_xor=sum(x['fg_xor'] for r in rows for x in r['renders']))
    json.dump(rep, open(os.path.join(HERE, 'surf3d_host.json'), 'w'), indent=1)
    print('counts identical (dims / voxels > 0.5 / surface points):', ok)
    print('render: %d foreground pixels, %d differ by > 2, max abs %d, mean abs %.5f, foreground xor %d'
          % (tot, dif, rep['max_abs'], rep['mean_abs'], rep['fg_xor']))
    if ok:
        print('SURF3D_HOST_PASS')


if __name__ == '__main__':
    main()
