r"""ONE-TIME CardiacUDA TEST evaluation of programs 21 cardiac_rv (rv_pl1_5) / 22 cardiac_ra (ra_pl1_5)
(final_test_2026-10-25/prereg.json; split written 10-23 before training, results/split.json).
Writes the lock first; integer models (= the board: echonet/int8_gpu.py on the GPU, checked equal to the CPU reference
on all 275 VAL frames first, wallmotion/rh_logits.model) at the 128 tier on every TEST frame ->
  Dice at the native crop (common_rv.to_native, as quantize_rv.py on VAL), per site, P5;
  plausibility rules of cross_check.py (largest component, >= 15 % of the expert LV, left of it, < 10 % inside it).
Writes results/final_test/{lock.txt, final_test.json}.

    E:\PLD\skin\.venv\Scripts\python.exe -I final_test_rv.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'wallmotion'))
from common_rv import RESULTS, dice, grouped, load, to_native  # noqa: E402
from cross_check import rules  # noqa: E402
from rh_logits import TAGS, model  # noqa: E402

OUT = RESULTS / 'final_test'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lock = OUT / 'lock.txt'
    if lock.exists():
        sys.exit('already run: ' + lock.read_text(encoding='utf-8'))
    lock.write_text('%s CardiacUDA TEST, rv_pl1_5 / ra_pl1_5 integer, PREREG final_test_2026-10-25\n'
                    % datetime.now(timezone.utc).isoformat(), encoding='utf-8')
    rep, masks = dict(prereg='final_test_2026-10-25/prereg.json'), {}
    for k, tag in TAGS.items():
        im = model(tag)
        x, _, gts, meta = load('test', k)
        xin = np.repeat(np.asarray(x)[:, None, ::4, ::4], 3, 1)
        q = np.concatenate([im(xin[i:i + 64])[:, 0].to(torch.int8).cpu().numpy() for i in range(0, len(xin), 64)])
        m = q >= 1
        masks[k] = m
        per = [dice(to_native(m[i], gts[i].shape), gts[i]) for i in range(len(m))]
        g = grouped(per, meta)
        g.update(p5=float(np.percentile(per, 5)), median=float(np.median(per)), frames=len(per),
                 videos=len({(mm['folder'], mm['video']) for mm in meta}))
        rep[k] = g
        print(k, tag, json.dumps(g), flush=True)
    lv = np.asarray(load('test', 'lv')[1])[:, ::4, ::4]
    summ, _ = rules(masks['rv'], masks['ra'], lv)
    rep['plausibility_vs_expert_lv'] = summ
    rep['done_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / 'final_test.json').write_text(json.dumps(rep, indent=1), encoding='utf-8')
    lock.write_text(lock.read_text(encoding='utf-8') + rep['done_utc'] + ' done\n', encoding='utf-8')
    print('plausibility', json.dumps(summ))


if __name__ == '__main__':
    main()
