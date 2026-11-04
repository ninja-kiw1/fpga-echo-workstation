r"""Part E step 3: the strain window's real path against the board (server v5.19).

For the first N CAMUS VAL sequences: tcp_image_ui sequence.load_sequence (the window's NIfTI reader + 512 stretch) ->
strainwin.run_strain (jobs.strain over imagelink) -> tracked points / line / curve / ES values must equal the reference
outputs of compare_port.py (results/port_val.npz, = track_val.py = C host harness). Writes results/check_ui_board.json.
Test split: not used.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe check_ui_board.py [N] [--host 192.168.1.254]
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
UI = HERE.parent.parent / 'tcp_image' / 'tcp_image_ui'
sys.path.insert(0, str(UI))
sys.path.insert(0, str(HERE.parent / 'step2'))
import imagelink  # noqa: E402
import sequence  # noqa: E402
import strainwin  # noqa: E402
import track_val as tv  # noqa: E402
import strain_port as sp  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    host = sys.argv[sys.argv.index('--host') + 1] if '--host' in sys.argv else '192.168.1.254'
    if '--host' in sys.argv:
        args.remove(host)
    n = int(args[0]) if args else 4
    z = np.load(HERE / 'results' / 'port_val.npz')
    per = json.loads((HERE / 'results' / 'compare_port.json').read_text(encoding='utf-8'))['per'][:n]
    link = imagelink.ImageLink(host, 5060, timeout=30.0)
    rows = []
    for si, pr in enumerate(per):
        k = f'{si:03d}'
        p, view = pr['patient'], pr['view']
        frames, info = sequence.load_sequence(str(tv.CAMUS / 'database_nifti' / p / f'{p}_{view}_half_sequence.nii.gz'))
        t0 = time.perf_counter()
        d = strainwin.run_strain(link, frames, info)
        r = d['result']
        g = z[k + '_g']
        row = dict(seq=f'{p}_{view}', wall_s=time.perf_counter() - t0, board_ms=r['ms']['total'],
                   points=bool(np.array_equal(d['points'], z[k + '_q8'] / 256.0)),
                   line=bool(np.array_equal(d['line'], z[k + '_path'])),
                   curve=bool(np.array_equal(np.array(r['gls_pct']), g * 100.0)),
                   es=bool(r['es_raw_pct'] == g[-1] * 100.0 and r['es_cal_pct'] == g[-1] * 100.0 + sp.OFFSET_PP),
                   clean=r['clean'], clean_pl_eq_ps=r['clean_pl_eq_ps'], es_cal_pct=r['es_cal_pct'])
        rows.append(row)
        print(row, flush=True)
    ok = all(r['points'] and r['line'] and r['curve'] and r['es'] and r['clean'] == 'pl' and r['clean_pl_eq_ps']
             for r in rows)
    out = dict(rows=rows, verdict='STRAIN_UI_BOARD_PASS' if ok else 'STRAIN_UI_BOARD_FAIL')
    (HERE / 'results' / 'check_ui_board.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(out['verdict'])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
