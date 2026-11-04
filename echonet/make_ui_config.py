"""③ build tcp_image_ui/echonet_config.json for the workstation from TRAINING-set decisions only:
  * beat-analysis parameters + linear EF calibration (full / real time) from results/beats_config_<features>.json;
  * quality-control thresholds = the 80th / 95th percentiles of the QC score over the 1,500 TRAIN-subset videos
    (level 1 "注意" above the first, level 2 "不可靠" above the second).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I make_ui_config.py --features t3_int
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402

from beats import analyze   # noqa: E402
from common import RESULTS   # noqa: E402
from eval_ef import qc_score   # noqa: E402

UI = ROOT.parent / 'tcp_image' / 'tcp_image_ui' / 'echonet_config.json'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--features', required=True)
    args = ap.parse_args()
    conf = json.loads((RESULTS / f'beats_config_{args.features}.json').read_text(encoding='utf-8'))
    d = RESULTS / 'frames'
    feat = np.load(d / f'{args.features}_trainsub.npz')
    index = json.loads((d / f'{args.features}_trainsub.json').read_text(encoding='utf-8'))['videos']
    scores = []
    for v, e in index.items():
        r = analyze(feat['vol'][e['start']:e['start'] + e['n']], e['fps'], 1, conf['cfg'])
        scores.append(qc_score(feat, e['start'], e['n'], r)[1])
    th = [float(np.percentile(scores, 80)), float(np.percentile(scores, 95))]
    out = dict(source=f'echonet/results/beats_config_{args.features}.json (TRAIN subset, 1,500 videos)',
               cfg=conf['cfg'], full=dict(a=conf['full']['a'], b=conf['full']['b']),
               realtime=dict(a=conf['realtime']['a'], b=conf['realtime']['b']), qc_thresholds=th,
               qc_note='QC score percentiles 80 / 95 on the TRAIN subset')
    UI.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
