"""F3 model selection on the validation sequences (test untouched), frozen in results/selection_f3.json.

Rule (fixed before looking at the final candidates): score = 0.5 * (forward ES Dice +
backward ED Dice), forward from the F1 board result at ED, backward from it at ES,
expert frames only. Baselines use the INTEGER models (= board): F1 on every frame
independently, and the F2 click model reused with B = 128. Candidates (train_seq.py
runs) are compared in FP32 on the GPU; the chosen one is then quantized and its integer
numbers come from quantize_seq.py.

    python compare_f3.py seq_prev seq_none seq_prev_ro seq_none_ro seq_prev_ex seq_none_ex
"""
import json
import sys
from datetime import datetime

import numpy as np
import torch

from common import RESULTS
from int8_reference import run_integer
from models.get_model import get_arch
from seqeval import load_val_seq, run_both, sequence_metrics, summarize

KEYS = ('ed_dice', 'es_dice', 'fac_abs_err_pct', 'fac_bias_pct', 'fac_auto_abs_err_pct', 'fac_auto_bias_pct',
        'keyframe_exact_ed', 'keyframe_exact_es', 'jump_mean', 'jump_p95', 'roughness')


def int_predict(tag, batch=25):
    d = RESULTS / 'quant' / tag
    plan = json.loads((d / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(d / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    def p(rgb):
        outs = []
        with torch.inference_mode():
            for i in range(0, len(rgb), batch):
                q = torch.from_numpy(np.ascontiguousarray(rgb[i:i + batch])).permute(0, 3, 1, 2)
                outs.append(run_integer(plan, arrays, q)[0][:, 0].numpy() >= 1)
        return np.concatenate(outs)
    return p


def main():
    torch.set_num_threads(8)
    V = load_val_seq()
    out = dict(frozen_at=datetime.now().isoformat(), rule='score = 0.5 * (fwd ES Dice + bwd ED Dice), validation, expert frames',
               baselines={}, candidates={})
    f1 = int_predict('lv_r128')(np.repeat(V['img'][..., None], 3, -1))
    masks = [f1[s['start']:s['start'] + s['length']] for s in V['index']]
    sf = summarize(sequence_metrics(V, masks))
    out['baselines']['f1_every_frame_int'] = dict(fwd=sf, bwd=sf, score=0.5 * (sf['es_dice'] + sf['ed_dice']))
    r, _ = run_both(int_predict('inter2_r128'), V, 'none')
    out['baselines']['f2_model_reused_int'] = r
    dev = torch.device('cuda')
    for tag in sys.argv[1:]:
        ck = torch.load(RESULTS / 'train' / tag / 'best.pth', weights_only=True)
        m = get_arch('unet').to(dev).eval()
        m.load_state_dict(ck['model_state_dict'])

        def predict(rgb):
            with torch.no_grad():
                return (m(torch.from_numpy(rgb).to(dev).permute(0, 3, 1, 2).float() / 255)[:, 0] > 0).cpu().numpy()
        r, _ = run_both(predict, V, ck['enc'])
        summ = json.loads((RESULTS / 'train' / tag / 'summary.json').read_text(encoding='utf-8')) \
            if (RESULTS / 'train' / tag / 'summary.json').exists() else None
        r.update(enc=ck['enc'], iter=ck['iter'], args=summ['args'] if summ else 'stopped early (see log)')
        out['candidates'][tag] = r
    best = max(out['candidates'], key=lambda t: out['candidates'][t]['score']) if out['candidates'] else None
    out['chosen'] = best
    (RESULTS / 'selection_f3.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(f"{'':24s} score  " + ' '.join(f'{k[:12]:>12s}' for k in KEYS))
    for group in ('baselines', 'candidates'):
        for name, r in out[group].items():
            for d in ('fwd', 'bwd'):
                print(f"{name[:20]:20s} {d} {r['score']:.4f} " + ' '.join(f'{r[d][k]:12.4f}' for k in KEYS))
    print('chosen', best)


if __name__ == '__main__':
    main()
