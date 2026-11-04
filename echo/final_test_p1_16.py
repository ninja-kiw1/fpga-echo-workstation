"""P1 1.6, ONE-TIME evaluation on the official CAMUS test split (50 patients): left-atrial volume (truncation flag +
TRAIN calibration), LV mass, wall thickness -- the same code as eval_p1_16.py (VAL), nothing tuned afterwards.

Rule (results/final_test_p1_16/lock.json, written 10-14 before training): the test split is evaluated once, after the
models, quantization, thresholds and measurement code are frozen on train / val. This script
  1. refuses to run unless lock.json says "locked";
  2. records the frozen inputs (quant plans + parameters of lv_r128 / epi_r128 / la_r128, cardiac_measure.py,
     ef_simpson.py, eval_p1_16.py, this file, la_calib.json, src/job_simpson.c sha256, LA_CAL, LA_EDGE_ROWS,
     thresholds + source) into lock.json and sets status "running" BEFORE touching a test frame;
  3. stage 1 (this interpreter, torch): the integer models (results/quant/<tag>, = the board) on the test frames ->
     results/final_test_p1_16/outputs/<target>_<i>.npy;
  4. stage 2 (--measure, srtp-vision interpreter: skimage, the one eval_p1_16 used on VAL, and no torch / MKL OpenMP
     clash): measurements -> results/final_test_p1_16/final_test.json, LOCK status "used".
A crash after step 2 leaves "running": do not re-run silently; report it.

    C:\\Users\\21888\\anaconda3\\python.exe final_test_p1_16.py --i-am-sure
"""
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone

import numpy as np

from common import RESULTS, load_cache
from eval_p1_16 import TAGS, evaluate

sys.path.insert(0, str(RESULTS.parent.parent / 'tcp_image' / 'tcp_image_ui'))
import cardiac_measure as cm  # noqa: E402

DST = RESULTS / 'final_test_p1_16'
LOCK = DST / 'lock.json'
OUT = DST / 'outputs'
UI = RESULTS.parent.parent / 'tcp_image' / 'tcp_image_ui'
RX = RESULTS.parent.parent / 'tcp_image' / 'tcp_image_rx'
MEASURE_PY = r'C:\Users\21888\anaconda3\envs\srtp-vision\python.exe'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_lock():
    return json.loads(LOCK.read_text(encoding='utf-8'))


def write_lock(lock):
    LOCK.write_text(json.dumps(lock, indent=1, default=str), encoding='utf-8')


def measure():
    lock = read_lock()
    if lock.get('status') != 'running':
        raise SystemExit('measure stage: LOCK status is %r, expected "running"' % lock.get('status'))
    res = evaluate('test', lambda k, i: np.load(OUT / f'{k}_{i:03d}.npy'))
    res['frozen_sha256'] = lock['frozen_sha256']
    (DST / 'final_test.json').write_text(json.dumps(res, indent=1, default=float), encoding='utf-8')
    lock.update(status='used', finished_utc=datetime.now(timezone.utc).isoformat())
    write_lock(lock)
    print(json.dumps(res['summary'], indent=1))


def main():
    if '--measure' in sys.argv:
        return measure()
    if '--i-am-sure' not in sys.argv:
        raise SystemExit('one-time test evaluation: pass --i-am-sure')
    lock = read_lock()
    if lock.get('status') != 'locked':
        raise SystemExit('lock.json status is %r, not "locked": the test split was already used' % lock.get('status'))
    frozen = {f'{t}/{f}': sha(RESULTS / 'quant' / t / f) for t in TAGS.values() for f in ('quant_plan.json', 'parameters.npz')}
    for p in (UI / 'cardiac_measure.py', UI / 'ef_simpson.py', RESULTS.parent / 'eval_p1_16.py',
              RESULTS.parent / 'final_test_p1_16.py', RESULTS / 'p1_16' / 'la_calib.json', RX / 'src' / 'job_simpson.c'):
        frozen[p.name] = sha(p)
    lock.update(status='running', started_utc=datetime.now(timezone.utc).isoformat(), frozen_sha256=frozen,
                la_cal=cm.LA_CAL, la_edge_rows=cm.LA_EDGE_ROWS,
                thresholds=dict(lavi=cm.LAVI_BANDS[:3], lvmi_2d=cm.LVMI_2D, wall_linear_cm=cm.WALL_LINEAR_CM,
                                density=cm.DENSITY, source=cm.SOURCE))
    write_lock(lock)
    t0 = time.time()
    import torch
    from int8_reference import run_integer
    torch.set_num_threads(8)
    x = load_cache('test', 'lv')[0]
    OUT.mkdir(exist_ok=True)
    for key, tag in TAGS.items():
        qd = RESULTS / 'quant' / tag
        plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
        with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        k = 1 << int(json.loads((qd / 'summary.json').read_text(encoding='utf-8'))['shift'])
        with torch.inference_mode():
            for i in range(x.shape[0]):
                g = torch.from_numpy(np.ascontiguousarray(x[i][::k, ::k]))
                q = g.unsqueeze(0).expand(3, -1, -1).unsqueeze(0).contiguous()
                np.save(OUT / f'{key}_{i:03d}.npy', run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int8))
        print(key, x.shape[0], f'{time.time() - t0:.0f}s', flush=True)
    sys.exit(subprocess.call([MEASURE_PY, str(RESULTS.parent / 'final_test_p1_16.py'), '--measure'],
                             cwd=str(RESULTS.parent)))


if __name__ == '__main__':
    main()
