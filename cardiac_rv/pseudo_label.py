"""RV / RA programs, step 5: filtered pseudo-labels on unlabelled-for-RV/RA frames of the demo datasets (TRAIN only).

Sources (TRAIN splits only, so the VAL cross checks stay held out):
  EchoNet TRAIN traced ED / ES frames (echonet/data/cache/train_img112.npy channel 1 = frame t; expert LV
  train_lab112.npy), board chain 112 -> zero pad 8 -> 128; up to --n-echonet frames (seed 0).
  CAMUS TRAIN 4CH ED / ES (expert LV = 1, LA = 3), F1 chain host_stretch 512 -> [::4].
Teacher: FP32 checkpoints (--rv / --ra). Pseudo-label of a frame = the teacher's largest component, kept only if
  the static anatomy rules of cross_check.rules hold for BOTH programs (present, left of the LV, < 10 % inside the
  LV, RA below RV) and the teacher is confident: mean |p - 0.5| over the 128 tier >= the --conf-q quantile of the
  frames that passed the rules. Expert LV (and LA on CAMUS) pixels are removed from the pseudo-mask.
Output per target: results/pseudo/<name>_<target>.npz with x512 (uint8) / y512 (bool) in the training layout
  (EchoNet 128 frames repeated 4 x 4, so [::4, ::4] gives back the board input exactly; CAMUS at the UI 512).
The cycle-phase check (cross_check.cycle_check) does not use these rules, so it stays an independent signal.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I pseudo_label.py --rv rv_w --ra ra_w --name pl1
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_rv import PLD, RESULTS  # noqa: E402
from cross_check import largest, model, rules  # noqa: E402
from nifti import read_nifti  # noqa: E402


def probs(m, x128):
    import torch
    out = []
    for i in range(0, len(x128), 64):
        xb = torch.from_numpy(np.ascontiguousarray(x128[i:i + 64])).cuda().float().div(255)
        with torch.no_grad():
            out.append(torch.sigmoid(m(xb.unsqueeze(1).expand(-1, 3, -1, -1))[:, 0]).cpu().numpy())
    return np.concatenate(out)


def sources(n_echonet):
    from common import host_stretch
    E = PLD / 'echonet' / 'data' / 'cache'
    xe = np.load(E / 'train_img112.npy', mmap_mode='r')
    ye = np.load(E / 'train_lab112.npy', mmap_mode='r')
    pick = np.sort(np.random.default_rng(0).choice(len(xe), min(n_echonet, len(xe)), replace=False))
    x = np.pad(np.asarray(xe[pick, 1]), ((0, 0), (8, 8), (8, 8)))
    lv = np.pad(np.asarray(ye[pick]).astype(bool), ((0, 0), (8, 8), (8, 8)))
    out = dict(echonet=dict(x128=x, lv=lv, other=np.zeros_like(lv), x512=None))
    C = PLD / 'echo' / 'data' / 'camus'
    xs, x5, lvs, las = [], [], [], []
    for p in (C / 'database_split' / 'subgroup_training.txt').read_text().split():
        for ph in ('ED', 'ES'):
            img, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_{ph}.nii.gz')
            gt, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_{ph}_gt.nii.gz')
            s = host_stretch(np.clip(img, 0, 255).astype(np.uint8))
            g = np.array(Image.fromarray(gt.astype(np.uint8)).resize((512, 512), Image.NEAREST))
            x5.append(s)
            xs.append(s[::4, ::4])
            lvs.append(g[::4, ::4] == 1)
            las.append(g[::4, ::4] == 3)
    out['camus'] = dict(x128=np.stack(xs), lv=np.stack(lvs), other=np.stack(las), x512=np.stack(x5))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--rv', required=True)
    ap.add_argument('--ra', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--n-echonet', type=int, default=4000, help='x512 + y512 cost 0.5 MB per frame on the GPU')
    ap.add_argument('--conf-q', type=float, default=0.3, help='drop the least confident fraction of rule-passing frames')
    args = ap.parse_args()
    od = RESULTS / 'pseudo'
    od.mkdir(parents=True, exist_ok=True)
    mrv, mra = model(args.rv), model(args.ra)
    report = dict(args=vars(args))
    packs = {'rv': ([], []), 'ra': ([], [])}
    for src, d in sources(args.n_echonet).items():
        prv, pra = probs(mrv, d['x128']), probs(mra, d['x128'])
        _, per = rules(prv > 0.5, pra > 0.5, d['lv'])
        ok = np.array([r['pair_ok'] for r in per])
        conf = {t: np.array([np.abs(p - 0.5).mean() for p in pp]) for t, pp in (('rv', prv), ('ra', pra))}
        c = np.minimum(conf['rv'], conf['ra'])
        thr = np.quantile(c[ok], args.conf_q) if ok.any() else 1.0
        keep = ok & (c >= thr)
        report[src] = dict(frames=len(ok), rules_pass=int(ok.sum()), kept=int(keep.sum()), conf_threshold=float(thr))
        for t, pp in (('rv', prv), ('ra', pra)):
            xs, ys = packs[t]
            for i in np.nonzero(keep)[0]:
                y = largest(pp[i] > 0.5) & ~d['lv'][i] & ~d['other'][i]
                if d['x512'] is None:      # EchoNet: 128 board input repeated 4 x 4
                    xs.append(np.repeat(np.repeat(d['x128'][i], 4, 0), 4, 1))
                else:
                    xs.append(d['x512'][i])
                ys.append(np.repeat(np.repeat(y, 4, 0), 4, 1))
        print(src, report[src], flush=True)
    for t, (xs, ys) in packs.items():
        np.savez(od / f'{args.name}_{t}.npz', x512=np.stack(xs).astype(np.uint8), y512=np.stack(ys))
        report[f'{t}_frames'] = len(xs)
    (od / f'{args.name}.json').write_text(json.dumps(report, indent=1, default=float), encoding='utf-8')
    print(json.dumps(report, indent=1, default=float))


if __name__ == '__main__':
    main()
