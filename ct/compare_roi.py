"""⑤ stage 2: apply the pre-registered rule to the ROI fine-model candidates (FP32 fine, integer coarse, validation).

Rule (README "第二阶段", written before training): highest mean two-stage 3D Dice wins; stage 2 is adopted only if the
winner beats the single-stage pipeline (0.9491). Writes results/selection_roi.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I compare_roi.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAGS = ('roi_z3', 'roi_prior')


def main():
    rows = {}
    for t in TAGS:
        s = json.loads((ROOT / 'results' / 'train' / t / 'summary.json').read_text(encoding='utf-8'))
        b = s['best']
        rows[t] = dict(best_iter=b['iter'], dice=b['dice'], single=b['single'],
                       per_case={r['case']: dict(two=round(r['dice'], 4), single=round(r['single'], 4)) for r in b['per_case']},
                       last_dice=s['last']['dice'])
    win = max(TAGS, key=lambda t: rows[t]['dice'])
    w = rows[win]
    out = dict(rule='highest mean validation two-stage 3D Dice; adopt only if above single stage', candidates=rows,
               chosen=win, adopt_stage2=w['dice'] > w['single'],
               cases_better_than_single=sum(v['two'] > v['single'] for v in w['per_case'].values()),
               cases_worse_than_single=sum(v['two'] < v['single'] for v in w['per_case'].values()),
               note='checkpoint picked on the same validation cases (20 evaluations), so the gain is optimistic; '
                    'the test set decides')
    (ROOT / 'results' / 'selection_roi.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in out.items() if k != 'candidates'}, indent=1))


if __name__ == '__main__':
    main()
