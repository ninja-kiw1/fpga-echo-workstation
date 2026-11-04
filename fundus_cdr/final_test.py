"""Part D one-time test: Chakshu official test split (336) + PAPILA (488, external camera, two experts).

Run ONCE, after the models and every rule are frozen. Order:
  1. refuse if results/final_test/lock.json exists;
  2. write lock.json with status "running" + sha256 of: the three model plans / parameters, cdr_ref.py, the
     deploy tables, both test caches; nothing has been inferred at that point;
  3. integer models (= board) through cdr_ref.run_case on every test image;
  4. metrics, results/final_test/summary.json + per_image.json; LOCK status -> "used".
References: Chakshu = majority-vote masks (5 experts); PAPILA = each expert separately (Dice averaged over the two)
and the mean of the two experts' vCDR. Nothing is tuned from these numbers.

    python -I prep_chaksu.py --test        (builds the test cache; computes no metric)
    python -I final_test.py --stage1 X --disc Y --cup Z
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402

import cdr_ref                           # noqa: E402
from common import CACHE, RESULTS, ROOT, load_cache, seg_metrics   # noqa: E402
from eval_val import IntProg, stats      # noqa: E402

OUT = RESULTS / 'final_test'
LOCK = OUT / 'lock.json'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(data, progs, experts):
    per = []
    for i, m in enumerate(data['meta']):
        calls = [0]

        def prog(name, pay):
            if name == 'disc':
                calls[0] += 1
                return progs['stage1' if calls[0] == 1 else 'disc'](pay)
            return progs['cup'](pay)
        r = cdr_ref.run_case(np.asarray(data['img512'][i]), prog)
        ref = float(np.mean([m[f'vcdr_{e}'] for e in experts])) if experts else m['vcdr']
        row = dict(name=m['name'], device=m['device'], ok=bool(r['ok']), vcdr_ref=ref)
        cd, cc = np.zeros((512, 512), bool), np.zeros((512, 512), bool)
        if r['ok']:
            y0, x0, s = r['y0'], r['x0'], r['side']
            cd[y0:y0 + s, x0:x0 + s] = r['disc']
            cc[y0:y0 + s, x0:x0 + s] = r['cup']
            row.update(side=s, vcdr=r['vcdr'], err=r['vcdr'] - ref)
        else:
            row.update(error=r['error'], err=None)
        sets = [f'_{e}' for e in experts] if experts else ['']
        row['dice_disc'] = float(np.mean([seg_metrics(cd, data[f'disccells{s}'][i], data['rows'][i], data['cols'][i])['dice']
                                          for s in sets]))
        row['dice_cup'] = float(np.mean([seg_metrics(cc, data[f'cupcells{s}'][i], data['rows'][i], data['cols'][i])['dice']
                                         for s in sets]))
        per.append(row)
    ok = [r for r in per if r['ok']]
    pv = np.array([(r['vcdr'], r['vcdr_ref']) for r in ok])
    out = dict(n=len(per), found=len(ok), dice_disc=float(np.mean([r['dice_disc'] for r in per])),
               dice_cup=float(np.mean([r['dice_cup'] for r in per])), vcdr=stats([r['err'] for r in per]))
    out['vcdr']['pearson_r'] = float(np.corrcoef(pv.T)[0, 1])
    for dev in sorted({r['device'] for r in per}):
        rr = [r for r in per if r['device'] == dev]
        out[f'device={dev}'] = dict(n=len(rr), dice_disc=float(np.mean([r['dice_disc'] for r in rr])),
                                    dice_cup=float(np.mean([r['dice_cup'] for r in rr])), vcdr=stats([r['err'] for r in rr]))
    ex = sorted(k for k in data['meta'][0] if k.startswith('vcdr_e'))
    if experts:
        out['inter_observer'] = stats([m['vcdr_e1'] - m['vcdr_e2'] for m in data['meta']])
    else:
        out['inter_observer_vs_majority'] = {k: stats([m[k] - m['vcdr'] for m in data['meta'] if k in m]) for k in ex}
    return out, per


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--stage1', required=True)
    ap.add_argument('--disc', required=True)
    ap.add_argument('--cup', required=True)
    a = ap.parse_args()
    if LOCK.exists():
        raise SystemExit(f'{LOCK} exists: the test sets were already used ({LOCK.read_text(encoding="utf-8")[:300]})')
    tags = dict(stage1=a.stage1, disc=a.disc, cup=a.cup)
    files = {}
    for role, t in tags.items():
        files[f'{role}_quant_plan'] = RESULTS / 'quant' / t / 'quant_plan.json'
        files[f'{role}_parameters'] = RESULTS / 'quant' / t / 'parameters.npz'
        files[f'{role}_network_txt'] = ROOT / 'deploy' / t / 'network.txt'
    files['cdr_ref'] = ROOT / 'cdr_ref.py'
    for name in ('chaksu_test', 'papila'):
        for p in sorted(CACHE.glob(f'{name}_*')):
            files[p.name] = p
    OUT.mkdir(parents=True, exist_ok=True)
    lock = dict(status='running', started_utc=datetime.now(timezone.utc).isoformat(), models=tags,
                sha256={k: sha(p) for k, p in files.items()},
                note='written before any test inference; part D test sets are used exactly once')
    LOCK.write_text(json.dumps(lock, indent=1), encoding='utf-8')

    progs = {role: IntProg(t) for role, t in tags.items()}
    res = dict(models=tags)
    per_all = {}
    res['chaksu_test'], per_all['chaksu_test'] = run(load_cache('chaksu_test'), progs, None)
    print('chaksu_test', json.dumps(res['chaksu_test'], indent=1), flush=True)
    res['papila'], per_all['papila'] = run(load_cache('papila'), progs, ['e1', 'e2'])
    print('papila', json.dumps(res['papila'], indent=1), flush=True)
    (OUT / 'summary.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    (OUT / 'per_image.json').write_text(json.dumps(per_all, indent=1), encoding='utf-8')
    lock.update(status='used', finished_utc=datetime.now(timezone.utc).isoformat(), summary_sha256=sha(OUT / 'summary.json'))
    LOCK.write_text(json.dumps(lock, indent=1), encoding='utf-8')
    print('FINAL_TEST_DONE')


if __name__ == '__main__':
    main()
