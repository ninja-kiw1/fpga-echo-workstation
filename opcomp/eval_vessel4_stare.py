"""Classical vs learned on the SAME PL engine: compiled line detector (op_vessel4) vs the trained fundus model.

Both run on the board at 512 x 512 (compact RGB frames, model 13 and model 0). STARE 20 images,
AH manual labels, the project's protocol (RGB -> 512 x 512, label nearest, full canvas).
  * AUC from the int8 outputs (threshold-free; the classical response is not a probability);
  * Dice: learned model at its deployed threshold (logit >= -3); classical at a threshold chosen
    by 2-fold cross-validation (pick on images 1-10, test on 11-20, and the other way round).
The learned model was trained on DRIVE (frozen weights); STARE was used before as a development
set for this checkpoint (see lwnet/results/stare_fp32/report.md), so neither number is a clean test.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe eval_vessel4_stare.py
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402
from PIL import Image       # noqa: E402

import imagelink            # noqa: E402
import proto                # noqa: E402

DATA = PLD / 'medical_fpga' / 'data' / 'stare' / 'png'
FOV = PLD / 'lwnet' / 'data' / 'STARE' / 'mask'


def fov_mask(rgb512):
    """Field of view: red channel > 25 (the background outside the fundus is black), largest component,
    holes filled. Derived here because masks are not available for all 20 images."""
    from scipy import ndimage
    m = rgb512[:, :, 0] > 25
    lab, n = ndimage.label(m)
    if n > 1:
        m = lab == (int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1)
    return ndimage.binary_fill_holes(m)


def auc(score, target):
    """ROC AUC by ranks (ties averaged)."""
    s = score.ravel().astype(np.float64)
    t = target.ravel()
    order = np.argsort(s, kind='mergesort')
    ranks = np.empty(len(s))
    sv = s[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1
        i = j + 1
    npos = t.sum()
    nneg = len(t) - npos
    return float((ranks[t].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def dice(pred, target):
    return float(2 * (pred & target).sum() / max(pred.sum() + target.sum(), 1))


def main():
    files = sorted((DATA / 'images').glob('*.png'))
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    rows, outs = [], {}
    t0 = time.time()
    for f in files:
        rgb = np.array(Image.open(f).convert('RGB').resize((512, 512), Image.LANCZOS))
        lab = np.array(Image.open(DATA / 'labels_ah' / f.name).resize((512, 512), Image.NEAREST)) > 127
        fov = fov_mask(rgb)
        res = {}
        for name, model, thr, w in (('learned', 0, -3, 512), ('classical', 13, 0, 512),
                                    ('classical256', 13, 0, 256), ('classical128', 13, 0, 128)):
            src = rgb if w == 512 else np.array(Image.open(f).convert('RGB').resize((w, w), Image.LANCZOS))
            st, lg, _, _ = link.v6c_round_trip(np.ascontiguousarray(src).tobytes(), w, kind=proto.V6C_KIND_RGB,
                                               model0=model, thr=thr, flags=proto.FLAG_STATS | proto.FLAG_C_LOGIT)
            assert st['model'] == model and st['ai_res'] == w, st
            out = np.frombuffer(lg, np.int8).reshape(w, w).astype(np.int16)
            k = 512 // w
            res[name] = (np.repeat(np.repeat(out, k, 0), k, 1), st['ai_us'] / 1000)
        outs[f.stem] = (res, lab, fov)
        row = dict(image=f.stem, dice_learned=dice(res['learned'][0] >= -3, lab),
                   dice_learned_fov=dice((res['learned'][0] >= -3) & fov, lab & fov),
                   ms_learned=res['learned'][1], ms_classical=res['classical'][1], ms_classical128=res['classical128'][1])
        for name in res:
            row['auc_' + name] = auc(res[name][0], lab)
            row['auc_fov_' + name] = auc(res[name][0][fov], lab[fov])
        rows.append(row)
        print(f.stem, {k: round(v, 4) for k, v in rows[-1].items() if k != 'image'}, f'({time.time() - t0:.0f}s)', flush=True)

    names = [r['image'] for r in rows]
    folds = (names[:10], names[10:])

    def fov_dice(n, key, thr):
        res, lab, fov = outs[n]
        return dice((res[key][0] >= thr) & fov, lab & fov)

    best_key = max(('classical', 'classical256', 'classical128'),
                   key=lambda k: np.mean([r['auc_fov_' + k] for r in rows]))
    cv = {}
    for dev, test in (folds, folds[::-1]):       # threshold chosen on the other half only
        best = max(range(-60, 127), key=lambda t: np.mean([fov_dice(n, best_key, t) for n in dev]))
        for n in test:
            cv[n] = (best, fov_dice(n, best_key, best))
    for r in rows:
        r['classical_threshold_cv'], r['dice_fov_classical_cv'] = cv[r['image']]
    summary = dict(images=len(rows), protocol='RGB LANCZOS 512, AH labels nearest 512, full canvas, both programs on the board',
                   auc={k: float(np.mean([r['auc_' + k] for r in rows]))
                        for k in ('learned', 'classical', 'classical256', 'classical128')},
                   auc_within_fov={k: float(np.mean([r['auc_fov_' + k] for r in rows]))
                                   for k in ('learned', 'classical', 'classical256', 'classical128')},
                   dice_learned_deployed_threshold=float(np.mean([r['dice_learned'] for r in rows])),
                   dice_learned_within_fov=float(np.mean([r['dice_learned_fov'] for r in rows])),
                   classical_best_scale_by_fov_auc=best_key,
                   dice_within_fov_classical_2fold_cv=float(np.mean([r['dice_fov_classical_cv'] for r in rows])),
                   fov='red channel > 25, largest component, holes filled (masks are not available for all 20 images)',
                   board_ms_classical128=float(np.mean([r['ms_classical128'] for r in rows])),
                   board_ms_learned=float(np.mean([r['ms_learned'] for r in rows])),
                   board_ms_classical=float(np.mean([r['ms_classical'] for r in rows])),
                   note='learned = frozen DRIVE checkpoint; STARE was already used as a development set for it',
                   per_image=rows)
    (ROOT / 'results' / 'vessel4_stare.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'per_image'}, indent=1))
    # side-by-side figure for one image (input / learned / classical / label)
    n = 'im0001'
    res, lab, _ = outs[n]
    rgb = np.array(Image.open(DATA / 'images' / f'{n}.png').convert('RGB').resize((512, 512), Image.LANCZOS))
    to8 = lambda a, lo, hi: np.clip((a - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    tiles = [rgb, np.repeat(to8(res['learned'][0], -40, 40)[..., None], 3, -1),
             np.repeat(to8(res[best_key][0], 0, 40)[..., None], 3, -1), np.repeat((lab * 255).astype(np.uint8)[..., None], 3, -1)]
    Image.fromarray(np.concatenate(tiles, 1)).resize((1024, 256), Image.BILINEAR).save(ROOT / 'results' / 'vessel4_vs_learned_im0001.png')


if __name__ == '__main__':
    main()
