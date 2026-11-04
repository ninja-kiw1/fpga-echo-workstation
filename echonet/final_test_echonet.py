"""③ the ONE evaluation on the official TEST split (1,277 videos). Everything was fixed on TRAIN / VAL before:
segmentation model t3 (integer, = the board), beat parameters + calibration + QC thresholds (TRAIN subset, integer).

Order: write the lock file first, then
  1. LV Dice on every TEST ED / ES frame (integer model on the GPU, int8_gpu, == CPU reference);
  2. per-frame features of every TEST video (infer_videos.py, integer);
  3. beat-to-beat EF for both modes + diagnosis + quality control (eval_ef.py logic with --skip-lock).
Writes results/final_test/ (lock, frames Dice, EF json) and prints a summary.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I final_test_echonet.py
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import PAD, RESULTS, load_frames   # noqa: E402
from int8_gpu import IntegerModel   # noqa: E402
from train_echonet import dice_batch, to_input   # noqa: E402

LOCK = RESULTS / 'final_test' / 'test_set_used.txt'


def main():
    if LOCK.exists():
        sys.exit('test set already used: ' + LOCK.read_text(encoding='utf-8'))
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text('%s  model=t3 (integer) config=beats_config_t3_int.json, everything fixed on TRAIN / VAL before\n'
                    % datetime.now(timezone.utc).isoformat(), encoding='utf-8')
    qd = RESULTS / 'quant' / 't3'
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        im = IntegerModel(plan, {k: z[k] for k in z.files})
    xte, yte, mte = load_frames('test')
    x = to_input(np.asarray(xte), 't3')
    with torch.no_grad():
        q = np.concatenate([im(x[i:i + 128])[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                            for i in range(0, len(x), 128)])
    d = dice_batch(q >= 1, np.asarray(yte).astype(bool))
    ph = np.array([m['phase'] for m in mte['frames']])
    seg = dict(frames=len(d), videos=len(d) // 2, dice=float(d.mean()), ed=float(d[ph == 'ED'].mean()),
               es=float(d[ph == 'ES'].mean()), p05=float(np.percentile(d, 5)), median=float(np.median(d)))
    (RESULTS / 'final_test' / 'test_segmentation.json').write_text(json.dumps(seg, indent=1), encoding='utf-8')
    print('TEST segmentation', seg, flush=True)
    py = sys.executable
    subprocess.run([py, '-I', str(ROOT / 'infer_videos.py'), '--quant', 'results/quant/t3', '--split', 'test'],
                   cwd=str(ROOT), check=True)
    subprocess.run([py, '-I', str(ROOT / 'eval_ef.py'), '--features', 't3_int', '--config', 'beats_config_t3_int.json',
                    '--split', 'test', '--final-test', '--skip-lock'], cwd=str(ROOT), check=True)


if __name__ == '__main__':
    main()
