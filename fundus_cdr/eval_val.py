"""Part D VAL evaluation of the whole chain (cdr_ref.run_case) with the INTEGER models (= board), plus FP32 for reference.

Per VAL image: stage 1 (disc program on box4) -> window -> stage 2 (disc + cup programs) -> cleaned masks -> vCDR.
Metrics against the Chaksu majority-vote masks at the ORIGINAL resolution:
  disc / cup Dice (window masks pasted on the 512 grid, NEAREST to the original size, exact cell counts),
  vCDR error (prediction - reference), MAE, bias, Pearson r; reference vCDR = rows spanned on the original masks.
Context: each expert's vCDR vs the majority vCDR (inter-observer spread), from prep_chaksu.py.
Writes results/eval/<name>/ : summary.json, per_image.json, cases.npz (q1 / qd / qc int8 logits + the img512
index, the C harness input for test/fundus_cdr/compare.py).

    python -I eval_val.py --disc disc_mixed_a --cup cup_roi_a [--stage1 disc_full_a] --name v1
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
import torch                             # noqa: E402

import cdr_ref                           # noqa: E402
from common import RESULTS, load_cache, seg_metrics   # noqa: E402
from int8_reference import run_integer   # noqa: E402
from models.get_model import get_arch    # noqa: E402


class IntProg(object):
    def __init__(self, tag):
        q = RESULTS / 'quant' / tag
        self.plan = json.loads((q / 'quant_plan.json').read_text(encoding='utf-8'))
        with np.load(q / 'parameters.npz', allow_pickle=False) as z:
            self.arrays = {k: z[k] for k in z.files}

    def __call__(self, pay):
        x = torch.from_numpy(np.ascontiguousarray(pay)).permute(2, 0, 1).unsqueeze(0).contiguous()
        with torch.inference_mode():
            return run_integer(self.plan, self.arrays, x)[0][0, 0].numpy().astype(np.int8)


class FpProg(object):
    """FP32 model with the board's decision rule mimicked: logit > 0 -> int8 1 / -1 (threshold 1)."""
    def __init__(self, tag):
        ck = torch.load(RESULTS / 'train' / tag / 'best.pth', weights_only=True, map_location='cpu')
        self.m = get_arch('unet').eval()
        self.m.load_state_dict(ck['model_state_dict'])

    def __call__(self, pay):
        x = torch.from_numpy(np.ascontiguousarray(pay)).permute(2, 0, 1).unsqueeze(0).float() / 255
        with torch.inference_mode():
            v = self.m(x)[0, 0].numpy()
        return np.clip(np.round(v * 4), -127, 127).astype(np.int8)       # sign-preserving stand-in for logits


def stats(err):
    e = np.asarray([v for v in err if v is not None], float)
    return dict(n=len(e), mae=float(np.mean(np.abs(e))), bias=float(np.mean(e)), sd=float(np.std(e)),
                p90_abs=float(np.percentile(np.abs(e), 90)))


def evaluate(data, progs):
    per, cases = [], dict(q1=[], qd=[], qc=[])
    for i, m in enumerate(data['meta']):
        img = np.asarray(data['img512'][i])

        def prog(name, pay, _stage=[0]):
            if name == 'disc':
                _stage[0] += 1
                return progs['stage1' if _stage[0] == 1 else 'disc'](pay)
            return progs['cup'](pay)
        r = cdr_ref.run_case(img, prog)
        row = dict(name=m['name'], device=m['device'], ok=bool(r['ok']), vcdr_ref=m['vcdr'])
        if r['ok']:
            canvas_d = np.zeros((512, 512), bool)
            canvas_c = np.zeros((512, 512), bool)
            y0, x0, s = r['y0'], r['x0'], r['side']
            canvas_d[y0:y0 + s, x0:x0 + s] = r['disc']
            canvas_c[y0:y0 + s, x0:x0 + s] = r['cup']
            row.update(side=s, y0=y0, x0=x0, vcdr=r['vcdr'], disc_h=r['disc_h'], cup_h=r['cup_h'],
                       dice_disc=seg_metrics(canvas_d, data['disccells'][i], data['rows'][i], data['cols'][i])['dice'],
                       dice_cup=seg_metrics(canvas_c, data['cupcells'][i], data['rows'][i], data['cols'][i])['dice'],
                       err=r['vcdr'] - m['vcdr'])
            cases['qd'].append(r['qd'])
            cases['qc'].append(r['qc'])
        else:
            row.update(error=r['error'], dice_disc=0.0, dice_cup=0.0, err=None)
            cases['qd'].append(np.zeros((128, 128), np.int8))
            cases['qc'].append(np.zeros((128, 128), np.int8))
        cases['q1'].append(r['q1'])
        per.append(row)
    return per, {k: np.stack(v).astype(np.int8) for k, v in cases.items()}


def summarize(per, meta):
    ok = [r for r in per if r['ok']]
    out = dict(n=len(per), found=len(ok), dice_disc=float(np.mean([r['dice_disc'] for r in per])),
               dice_cup=float(np.mean([r['dice_cup'] for r in per])), vcdr=stats([r['err'] for r in per]))
    pv = [(r['vcdr'], r['vcdr_ref']) for r in ok]
    out['vcdr']['pearson_r'] = float(np.corrcoef(np.array(pv).T)[0, 1]) if len(pv) > 2 else None
    for dev in sorted({r['device'] for r in per}):
        rr = [r for r in per if r['device'] == dev]
        out[f'device={dev}'] = dict(n=len(rr), dice_disc=float(np.mean([r['dice_disc'] for r in rr])),
                                    dice_cup=float(np.mean([r['dice_cup'] for r in rr])),
                                    vcdr=stats([r['err'] for r in rr]))
    ex = [k for k in meta[0] if k.startswith('vcdr_e')]
    out['inter_observer_vs_majority'] = {k: stats([m[k] - m['vcdr'] for m in meta]) for k in ex}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--disc', required=True)
    ap.add_argument('--cup', required=True)
    ap.add_argument('--stage1', help='separate stage-1 model tag (default: --disc)')
    ap.add_argument('--name', required=True)
    ap.add_argument('--split', default='chaksu_val')
    ap.add_argument('--fp32', action='store_true', help='also run the FP32 models')
    a = ap.parse_args()
    assert a.split == 'chaksu_val', 'test splits are evaluated only by final_test.py'
    data = load_cache(a.split)
    out = RESULTS / 'eval' / a.name
    out.mkdir(parents=True, exist_ok=True)
    s1 = a.stage1 or a.disc
    res = dict(models=dict(stage1=s1, disc=a.disc, cup=a.cup), split=a.split, test_set_used=False)
    per, cases = evaluate(data, dict(stage1=IntProg(s1), disc=IntProg(a.disc), cup=IntProg(a.cup)))
    res['integer'] = summarize(per, data['meta'])
    np.savez_compressed(out / 'cases.npz', **cases)
    (out / 'per_image.json').write_text(json.dumps(per, indent=1), encoding='utf-8')
    if a.fp32:
        perf, _ = evaluate(data, dict(stage1=FpProg(s1), disc=FpProg(a.disc), cup=FpProg(a.cup)))
        res['fp32'] = summarize(perf, data['meta'])
    (out / 'summary.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    main()
