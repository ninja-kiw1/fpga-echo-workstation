"""RV / RA programs, step 4: label-free plausibility on the datasets the demo will actually play (EchoNet, CAMUS).

Neither has RV / RA labels, so the check uses anatomy relative to the expert LV (and LA on CAMUS), on the 128 tier:
  present     largest component >= 15 % of the expert LV area
  side        RV / RA centroid left of the LV centroid (apex-up, LV-right convention of all three datasets)
  order       RA centroid below the RV centroid
  overlap_lv  fraction of the prediction inside the expert LV (should be ~0)
  plausible   present & side & overlap_lv < 0.1 (& order, for the pair)
Same rules on CardiacUDA VAL (where Dice is known) as the reference rate, plus its VAL Dice.
Independent of these static rules (so not satisfied "by construction" when pseudo-labels are filtered with them):
cycle_check() - RV area in phase and RA area in anti-phase with the LV area over time (EchoNet videos, CAMUS
half sequences). FP32 checkpoints (the integer model is
within ~0.003 Dice). Chains: EchoNet = program 16's (grey 112 -> zero pad 8 -> 128, no resampling); CAMUS = F1's
(host_stretch to 512 -> [::4]); CardiacUDA = prep_cardiacuda.py's. Frames: EchoNet VAL traced ED / ES (2,576),
CAMUS VAL 4CH ED / ES (100), CardiacUDA VAL labelled (275). Montage: results/cross_montage.png (plot step).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I cross_check.py [RV_TAG RA_TAG]
    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I cross_check.py plot [_RV_TAG_RA_TAG]
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_rv import PLD, RESULTS, load  # noqa: E402
from nifti import read_nifti  # noqa: E402

DEV = 'cuda'


def model(tag):
    import torch
    from models.get_model import get_arch
    ck = torch.load(RESULTS / 'train' / tag / 'best.pth', weights_only=True, map_location='cpu')
    m = get_arch('unet').to(DEV).eval()
    m.load_state_dict(ck['model_state_dict'])
    return m


def run(m, x128):
    import torch
    out = []
    for i in range(0, len(x128), 64):
        xb = torch.from_numpy(np.ascontiguousarray(x128[i:i + 64])).to(DEV).float().div(255)
        with torch.no_grad():
            out.append((m(xb.unsqueeze(1).expand(-1, 3, -1, -1))[:, 0] > 0).cpu().numpy())
    return np.concatenate(out)


def largest(m):
    lab, n = ndimage.label(m)
    if n > 1:
        m = lab == (int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1)
    return m


def centroid(m):
    r, c = np.nonzero(m)
    return (r.mean(), c.mean()) if len(r) else (np.nan, np.nan)


def rules(rv, ra, lv):
    out = []
    for a, b, l in zip(rv, ra, lv):
        a, b = largest(a), largest(b)
        la = max(int(l.sum()), 1)
        cl, ca, cb = centroid(l), centroid(a), centroid(b)
        r = dict(rv_present=a.sum() >= 0.15 * la, ra_present=b.sum() >= 0.15 * la,
                 rv_side=bool(ca[1] < cl[1]), ra_side=bool(cb[1] < cl[1]), order=bool(cb[0] > ca[0]),
                 rv_overlap_lv=float((a & l).sum() / max(a.sum(), 1)), ra_overlap_lv=float((b & l).sum() / max(b.sum(), 1)))
        r['rv_ok'] = bool(r['rv_present'] and r['rv_side'] and r['rv_overlap_lv'] < 0.1)
        r['ra_ok'] = bool(r['ra_present'] and r['ra_side'] and r['ra_overlap_lv'] < 0.1)
        r['pair_ok'] = bool(r['rv_ok'] and r['ra_ok'] and r['order'])
        out.append(r)
    keys = out[0].keys()
    return {k: float(np.mean([o[k] for o in out])) for k in keys}, out


def echonet():
    E = PLD / 'echonet' / 'data' / 'cache'
    x = np.asarray(np.load(E / 'val_img112.npy', mmap_mode='r')[:, 1])     # [V, (t - d, t, t + d)] -> frame t
    y = np.asarray(np.load(E / 'val_lab112.npy', mmap_mode='r')).astype(bool)
    return np.pad(x, ((0, 0), (8, 8), (8, 8))), np.pad(y, ((0, 0), (8, 8), (8, 8)))


def camus():
    from common import host_stretch
    C = PLD / 'echo' / 'data' / 'camus'
    pats = (C / 'database_split' / 'subgroup_validation.txt').read_text().split()
    xs, lv, la = [], [], []
    for p in pats:
        for ph in ('ED', 'ES'):
            img, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_{ph}.nii.gz')
            gt, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_{ph}_gt.nii.gz')
            xs.append(host_stretch(np.clip(img, 0, 255).astype(np.uint8))[::4, ::4])
            g = np.array(Image.fromarray(gt.astype(np.uint8)).resize((512, 512), Image.NEAREST))[::4, ::4]
            lv.append(g == 1)
            la.append(g == 3)
    return np.stack(xs), np.stack(lv), np.stack(la)


def cycle_check(mrv, mra, n_videos=150, max_frames=200):
    """physiology over time, independent of the static rules: per video, Pearson r of the RV / RA area curve with the
    LV area curve. Expected: RV in phase with LV (r > 0, both largest at end-diastole), RA in anti-phase (r < 0, the
    atria fill during systole). EchoNet VAL: 150 videos (seed 0), first 200 frames, LV = program 16 integer masks
    (t3_int_val.npz). CAMUS VAL 4CH half sequences (ED -> ES, 50), LV = per-frame expert half-sequence labels."""
    from common import host_stretch
    E = PLD / 'echonet'
    idx = json.loads((E / 'results' / 'frames' / 't3_int_val.json').read_text(encoding='utf-8'))['videos']
    vid = json.loads((E / 'data' / 'cache' / 'val_videos.json').read_text(encoding='utf-8'))
    frames = np.load(E / 'data' / 'cache' / 'val_videos.npy', mmap_mode='r')
    packed = np.load(E / 'results' / 'frames' / 't3_int_val.npz')['masks']
    names = sorted(idx)
    pick = np.random.default_rng(0).choice(len(names), n_videos, replace=False)
    out = {}
    r_rv, r_ra = [], []
    for j in pick:
        v = names[j]
        e, ev = idx[v], vid[v]
        n = min(e['n'], max_frames)
        x = np.pad(np.asarray(frames[ev['start']:ev['start'] + n]), ((0, 0), (8, 8), (8, 8)))
        lv = np.unpackbits(packed[e['start']:e['start'] + n], axis=1)[:, :112 * 112].reshape(-1, 112, 112).sum((1, 2))
        a_rv = np.array([largest(m).sum() for m in run(mrv, x)], float)
        a_ra = np.array([largest(m).sum() for m in run(mra, x)], float)
        for a, rr in ((a_rv, r_rv), (a_ra, r_ra)):
            rr.append(float(np.corrcoef(a, lv)[0, 1]) if a.std() > 0 and lv.std() > 0 else 0.0)
    out['echonet_val_videos'] = dict(n=len(pick), rv_r_median=float(np.median(r_rv)), rv_in_phase=float(np.mean(np.array(r_rv) > 0)),
                                     ra_r_median=float(np.median(r_ra)), ra_anti_phase=float(np.mean(np.array(r_ra) < 0)))
    C = PLD / 'echo' / 'data' / 'camus'
    r_rv, r_ra = [], []
    for p in (C / 'database_split' / 'subgroup_validation.txt').read_text().split():
        seq, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_half_sequence.nii.gz')
        gts, _ = read_nifti(C / 'database_nifti' / p / f'{p}_4CH_half_sequence_gt.nii.gz')
        x = np.stack([host_stretch(np.clip(f, 0, 255).astype(np.uint8))[::4, ::4] for f in seq])
        lv = (gts == 1).sum((1, 2)).astype(float)
        a_rv = np.array([largest(m).sum() for m in run(mrv, x)], float)
        a_ra = np.array([largest(m).sum() for m in run(mra, x)], float)
        for a, rr in ((a_rv, r_rv), (a_ra, r_ra)):
            rr.append(float(np.corrcoef(a, lv)[0, 1]) if a.std() > 0 and lv.std() > 0 else 0.0)
    out['camus_val_half_sequences'] = dict(n=len(r_rv), rv_r_median=float(np.median(r_rv)), rv_in_phase=float(np.mean(np.array(r_rv) > 0)),
                                           ra_r_median=float(np.median(r_ra)), ra_anti_phase=float(np.mean(np.array(r_ra) < 0)))
    return out


def main():
    rv_tag = sys.argv[1] if len(sys.argv) > 2 else 'rv_r128'
    ra_tag = sys.argv[2] if len(sys.argv) > 2 else 'ra_r128'
    mrv, mra = model(rv_tag), model(ra_tag)
    res, keep = {}, {}
    xv, _, _, meta = load('val', 'lv')
    lab = np.load(Path(__file__).resolve().parent / 'data' / 'cache' / 'val_lab512.npy', mmap_mode='r')
    x = np.asarray(xv)[:, ::4, ::4]
    res['cardiacuda_val'], _ = rules(run(mrv, x), run(mra, x), np.asarray(lab)[:, ::4, ::4] == 1)
    keep['cardiacuda_val'] = (x, run(mrv, x), run(mra, x), np.asarray(lab)[:, ::4, ::4] == 1)
    xe, ye = echonet()
    pe = (run(mrv, xe), run(mra, xe))
    res['echonet_val_ed_es'], _ = rules(*pe, ye)
    keep['echonet_val_ed_es'] = (xe, *pe, ye)
    xc, lc, lac = camus()
    pc = (run(mrv, xc), run(mra, xc))
    res['camus_val_4ch_ed_es'], per = rules(*pc, lc)
    res['camus_val_4ch_ed_es']['ra_overlap_la'] = float(np.mean([(largest(b) & l).sum() / max(largest(b).sum(), 1)
                                                                 for b, l in zip(pc[1], lac)]))
    keep['camus_val_4ch_ed_es'] = (xc, *pc, lc)
    res['cardiacuda_val']['dice_rv'] = val_dice(mrv, 'rv')
    res['cardiacuda_val']['dice_ra'] = val_dice(mra, 'ra')
    res.update(cycle_check(mrv, mra))
    suffix = '' if (rv_tag, ra_tag) == ('rv_r128', 'ra_r128') else f'_{rv_tag}_{ra_tag}'
    (RESULTS / f'cross_check{suffix}.json').write_text(json.dumps(dict(rv=rv_tag, ra=ra_tag, **res), indent=1), encoding='utf-8')
    for k, v in res.items():
        print(f"{k:26s} " + ' '.join(f'{a} {b:.3f}' for a, b in v.items()))
    montage(keep, suffix)


def val_dice(m, target):
    from common_rv import dice, to_native
    x, _, gts, _ = load('val', target)
    p = run(m, np.asarray(x)[:, ::4, ::4])
    return float(np.mean([dice(to_native(p[i], gts[i].shape), gts[i]) for i in range(len(gts))]))


def montage(keep, suffix=''):
    """the training venv has no matplotlib: save 8 random frames per dataset; `python cross_check.py plot` (any env
    with matplotlib, no torch needed at plot time) draws them"""
    rng = np.random.default_rng(0)
    arrs = {}
    for k, (x, rv, ra, lv) in keep.items():
        i = np.sort(rng.choice(len(x), 8, replace=False))
        arrs.update({f'{k}__x': x[i], f'{k}__rv': rv[i], f'{k}__ra': ra[i], f'{k}__lv': lv[i], f'{k}__i': i})
    np.savez_compressed(RESULTS / f'cross_samples{suffix}.npz', **arrs)


def plot(suffix=''):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    z = np.load(RESULTS / f'cross_samples{suffix}.npz')
    rows = sorted({k.split('__')[0] for k in z.files})
    fig, ax = plt.subplots(len(rows), 8, figsize=(20, 2.7 * len(rows)))
    for r, k in enumerate(rows):
        x, rv, ra, lv, ids = (z[f'{k}__{s}'] for s in ('x', 'rv', 'ra', 'lv', 'i'))
        for c in range(len(x)):
            i, a = c, ax[r, c]
            a.imshow(x[i], cmap='gray')
            ov = np.zeros(x[i].shape + (4,))
            ov[largest(rv[i])] = (0.5, 0.2, 0.9, 0.45)
            ov[largest(ra[i])] = (0.9, 0.1, 0.1, 0.45)
            a.imshow(ov)
            a.contour(lv[i], levels=[0.5], colors='orange', linewidths=0.8)
            a.set_title(f'{k} #{ids[c]}', fontsize=7)
            a.axis('off')
    fig.suptitle('purple = RV program, red = RA program, orange contour = expert LV', fontsize=10)
    fig.tight_layout()
    fig.savefig(RESULTS / f'cross_montage{suffix}.png', dpi=65)


if __name__ == '__main__':
    plot(sys.argv[2] if len(sys.argv) > 2 else '') if sys.argv[1:2] == ['plot'] else main()
