r"""Host check of src/strain.c (part E step 3) against E:/PLD/strain/step3/strain_port.py, bit for bit.

Inputs = strain/step3/results/port_val.npz (compare_port.py: CAMUS VAL 100 sequences, 512 frames, the integer
lv_r128 / epi_r128 outputs at ED; the port there already equals track_val.py at every step). The harness (WSL gcc,
-ffp-contract=off, plain C kernel) runs the chain; compared: return code, longest path, resampled points (f64), every
tracked Q8 position, every length and curve value (f64). Writes test/strain/strain_host.json. Test split: not used.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe compare_host.py
"""
import json
import os
import struct
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NPZ = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', 'strain', 'step3', 'results', 'port_val.npz'))
WORK = os.path.join(HERE, 'work')
# until server v5.18 (part A) is deployed the strain sources live in a staging folder (strain/step3/rx_src) so that
# a plain `deploy_board_v5.py prepare` of v5.18 does not pick them up; afterwards they are in src/
SRC = os.path.join(HERE, '..', '..', 'src')
if not os.path.exists(os.path.join(SRC, 'strain.c')):
    SRC = os.path.normpath(os.path.join(HERE, '..', '..', '..', '..', 'strain', 'step3', 'rx_src'))


def wsl(p):
    p = os.path.abspath(p).replace('\\', '/')
    return '/mnt/' + p[0].lower() + p[2:]


def main():
    t0 = time.time()
    os.makedirs(WORK, exist_ok=True)
    z = np.load(NPZ)
    keys = sorted({k.split('_')[0] for k in z.files})
    fin = os.path.join(WORK, 'in.bin')
    with open(fin, 'wb') as f:
        for k in keys:
            fr = z[k + '_frames']
            sx, sy = z[k + '_sxsy']
            f.write(struct.pack('<idd', len(fr), sx, sy))
            f.write(z[k + '_lv'].astype(np.uint8).tobytes())
            f.write(z[k + '_epi'].astype(np.uint8).tobytes())
            f.write(np.ascontiguousarray(fr, np.uint8).tobytes())
    cmd = (f"cd {wsl(HERE)} && gcc -std=gnu99 -O2 -ffp-contract=off -Wall -Wextra -I{wsl(SRC)} harness_strain.c "
           f"{wsl(SRC)}/strain.c {wsl(SRC)}/bm_sad.c -lm -o work/harness_strain && "
           f"./work/harness_strain work/in.bin work/out.bin")
    r = subprocess.run(['wsl', 'bash', '-lc', cmd], capture_output=True, text=True)
    print(r.stdout, r.stderr)
    assert r.returncode == 0, r.returncode
    b = open(os.path.join(WORK, 'out.bin'), 'rb').read()
    off, rows = 0, []
    for k in keys:
        rc, n, pl, edges, T = struct.unpack_from('<5i', b, off)
        off += 20
        row = dict(seq=k, rc=rc)
        if rc == 0:
            path = np.frombuffer(b, np.int32, 2 * pl, off).reshape(pl, 2)
            off += 8 * pl
            pts = np.frombuffer(b, np.float64, 2 * n, off).reshape(n, 2)
            off += 16 * n
            q8 = np.frombuffer(b, np.int32, 2 * n * T, off).reshape(T, n, 2)
            off += 8 * n * T
            L = np.frombuffer(b, np.float64, T, off)
            off += 8 * T
            g = np.frombuffer(b, np.float64, T, off)
            off += 8 * T
            row.update(n=n, T=T, edges=edges,
                       path=bool(np.array_equal(path, z[k + '_path'])),
                       pts=bool(np.array_equal(pts, z[k + '_pts0'])),
                       q8=bool(np.array_equal(q8, z[k + '_q8'])),
                       L=bool(np.array_equal(L, z[k + '_L'])),
                       g=bool(np.array_equal(g, z[k + '_g'])))
        rows.append(row)
    ok = all(r['rc'] == 0 and r['path'] and r['pts'] and r['q8'] and r['L'] and r['g'] for r in rows)
    summ = dict(n=len(rows), **{f: sum(1 for r in rows if r.get(f)) for f in ('path', 'pts', 'q8', 'L', 'g')},
                rc_nonzero=sum(1 for r in rows if r['rc'] != 0),
                verdict='STRAIN_HOST_PASS' if ok else 'STRAIN_HOST_FAIL', seconds=time.time() - t0)
    with open(os.path.join(HERE, 'strain_host.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(summary=summ, per=rows), f, indent=1)
    os.remove(fin)
    print(json.dumps(summ, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
