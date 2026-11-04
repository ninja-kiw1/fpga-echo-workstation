"""⑤ CT spleen: apply the pre-registered selection rule to the input-encoding candidates (FP32, validation only).

Rule (README, written before training): the candidate with the highest mean per-case 3D Dice on the 7 validation
cases (raw board mask, no post-processing) wins. Writes results/selection_input.json.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I compare_inputs.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAGS = ('rep_r128', 'z3_r128')


def main():
    rows = {}
    for t in TAGS:
        s = json.loads((ROOT / 'results' / 'train' / t / 'summary.json').read_text(encoding='utf-8'))
        b = s['best']
        rows[t] = dict(input=s['args']['input'], best_iter=b['iter'], dice=b['dice'], dice_lcc=b['dice_lcc'],
                       per_case={r['case']: round(r['dice'], 4) for r in b['per_case']})
    win = max(TAGS, key=lambda t: rows[t]['dice'])
    other = [t for t in TAGS if t != win][0]
    better = sum(rows[win]['per_case'][c] > rows[other]['per_case'][c] for c in rows[win]['per_case'])
    out = dict(rule='highest mean per-case 3D Dice on validation (FP32, raw mask)', candidates=rows, chosen=win,
               cases_better_than_other=f'{better}/{len(rows[win]["per_case"])}')
    (ROOT / 'results' / 'selection_input.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
