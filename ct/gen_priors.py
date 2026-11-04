"""⑤ stage 2: realistic coarse results for the TRAINING cases (priors for roi_prior, windows for both candidates).

Each training case is predicted by the cross-validation coarse model that did not see it (train_ct.py --fold k,
fixed schedule, last.pth). FP32 logits are put on the board's integer scale with model 14's output scale
(q = clip(round(logit / scale), -127, 127)), so the same coarse post-processing applies as for real board outputs.
Validation / test cases use the real integer outputs of model 14 instead (results/quant/z3_r128/outputs/).
Writes data/cache/cvq_<case>.npy and results/priors_cv.json (out-of-fold coarse Dice per case, for information).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I gen_priors.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import CACHE, RESULTS, dice3d, load_split   # noqa: E402
from models.get_model import get_arch   # noqa: E402
from roi import coarse_mask   # noqa: E402
from train_ct import case_inputs, load_set, predict_case   # noqa: E402


def main():
    device = torch.device('cuda')
    split = load_split()
    scale = json.loads((RESULTS / 'quant' / 'z3_r128' / 'summary.json').read_text(encoding='utf-8'))['output_scale']
    rows = {}
    for k in range(3):
        held = split['train'][k::3]
        ck = torch.load(RESULTS / 'train' / f'cv{k}_z3' / 'last.pth', weights_only=True, map_location='cpu')
        model = get_arch('unet').to(device).eval()
        model.load_state_dict(ck['model_state_dict'])
        data = load_set(held)
        for c, (a, b), x in zip(held, data['spans'], case_inputs(data, 'z3', 4)):
            lg = predict_case(model, x, device)
            q = np.clip(np.rint(lg / scale), -127, 127).astype(np.int8)
            np.save(CACHE / f'cvq_{c}.npy', q)
            rows[c] = dict(fold=k, dice_coarse_oof=dice3d(coarse_mask(q), data['lab'][a:b].astype(bool)))
            print(c, rows[c], flush=True)
    s = dict(mean_oof_dice=float(np.mean([r['dice_coarse_oof'] for r in rows.values()])),
             min_oof_dice=float(np.min([r['dice_coarse_oof'] for r in rows.values()])), per_case=rows)
    (RESULTS / 'priors_cv.json').write_text(json.dumps(s, indent=1), encoding='utf-8')
    print('out-of-fold coarse Dice mean %.4f min %.4f' % (s['mean_oof_dice'], s['min_oof_dice']))


if __name__ == '__main__':
    main()
