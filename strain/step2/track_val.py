"""Part E step 2 (3): host prototype of myocardial speckle tracking -> GLS-like curve, CAMUS VAL half sequences.

Chain (what the board would do, PS side):
  frames: CAMUS half sequence (ED -> ES), host LANCZOS stretch to 512 (UI path), tracking tier 512 or 256 (x[::2, ::2])
  blocks: myocardium at ED = epi - lv from the board-equivalent integer models (program 18 epi_r128, F1 lv_r128;
          results/quant/<tag>/outputs/val_*.npy, >= 1 at 128, x4 repeat, clean()); mid-wall line = longest path of the
          skeleton, resampled every STEP mm; one 16 x 16 block per point
  tracking: frame t -> t + 1 with the C kernel (bm_sad.dll = bm_sad.c, bm_track_points: full search +-R, raw-SAD argmin,
          parabolic or equiangular (V) sub-pixel), optional median of each point's displacement over its 5 line neighbours
  curve:  GLS-like(t) = L(t) / L(0) - 1 of the tracked mid-wall line (mm, original pixel spacing; optional moving
          average over 5 points before measuring, optional trim of the basal ends)
References (VAL expert masks, ED and ES only):  mid-wall line length change of label 2 (myocardium), endocardial
  border length change (LV pixels touching label 2), same line extraction at the same tier.
Also a synthetic check: ED frame warped by a known affine (longitudinal shortening to -15 %) -> exact strain known.
Test split: not used.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe track_val.py      (needs bm_sad.dll: see build_dll.sh)
"""
import ctypes
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from skimage.morphology import skeletonize

HERE = Path(__file__).resolve().parent
ECHO = HERE.parent.parent / 'echo'
sys.path.insert(0, str(ECHO))
from common import CACHE, CAMUS, LABELS, RESULTS, host_stretch  # noqa: E402
from nifti import read_nifti  # noqa: E402

sys.path.insert(0, str(HERE.parent.parent / 'tcp_image' / 'tcp_image_ui'))
from ef_simpson import clean  # noqa: E402

lib = ctypes.CDLL(str(HERE / 'bm_sad.dll'))
lib.bm_track_points.argtypes = [ctypes.c_void_p] * 2 + [ctypes.c_int] * 6 + [ctypes.c_void_p] * 4
lib.bm_track_points.restype = ctypes.c_int
lib.bm_track_line.argtypes = [ctypes.c_void_p] * 2 + [ctypes.c_int] * 7 + [ctypes.c_void_p] * 6
lib.bm_track_line.restype = ctypes.c_int


BM_OPT_VFIT = 2   # bm_sad.h


def track_step(A, B, xq, yq, R, opts, k=1):
    """one frame step for all points (bm_track_line: anchor = current position, SAD aggregation over k line
    neighbours; k = 1 is bm_track_points), in place on int32 Q8 arrays; returns (sad, flags)"""
    A = np.ascontiguousarray(A, np.uint8)
    B = np.ascontiguousarray(B, np.uint8)
    n = len(xq)
    sad = np.zeros(n, np.uint32)
    fl = np.zeros(n, np.int32)
    ax, ay = xq.copy(), yq.copy()
    h, w = A.shape
    rc = lib.bm_track_line(A.ctypes.data, B.ctypes.data, w, h, w, n, R, k, opts, ax.ctypes.data, ay.ctypes.data,
                           xq.ctypes.data, yq.ctypes.data, sad.ctypes.data, fl.ctypes.data)
    assert rc >= 0
    return sad, fl


# ---------------------------------------------------------------- line extraction
def longest_path(mask, sx, sy):
    """ordered (y, x) pixels of the longest geodesic path through a thin 8-connected set (tree-diameter trick)"""
    ys, xs = np.nonzero(mask)
    if len(ys) < 3:
        return None
    idx = -np.ones(mask.shape, np.int64)
    idx[ys, xs] = np.arange(len(ys))
    rows, cols, wts = [], [], []
    H, W = mask.shape
    for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
        y2, x2 = ys + dy, xs + dx
        ok = (y2 >= 0) & (y2 < H) & (x2 >= 0) & (x2 < W)
        j = np.full(len(ys), -1)
        j[ok] = idx[y2[ok], x2[ok]]
        ok = j >= 0
        rows += [np.arange(len(ys))[ok]]
        cols += [j[ok]]
        wts += [np.full(ok.sum(), np.hypot(dy * sy, dx * sx))]
    r, c, wv = np.concatenate(rows), np.concatenate(cols), np.concatenate(wts)
    g = coo_matrix((np.r_[wv, wv], (np.r_[r, c], np.r_[c, r])), shape=(len(ys), len(ys))).tocsr()
    d0 = dijkstra(g, indices=0)
    d0[~np.isfinite(d0)] = -1
    a = int(np.argmax(d0))
    da, pred = dijkstra(g, indices=a, return_predecessors=True)
    da[~np.isfinite(da)] = -1
    b = int(np.argmax(da))
    path = [b]
    while path[-1] != a:
        path.append(int(pred[path[-1]]))
    return np.stack([ys[path], xs[path]], 1).astype(float)


def resample(pts, sx, sy, step_mm, smooth=5):
    """moving-average smoothing, then points every step_mm along the line (in mm with spacing sx, sy)"""
    if smooth > 1 and len(pts) > smooth:
        k = np.ones(smooth) / smooth
        pad = smooth // 2
        p = np.pad(pts, ((pad, pad), (0, 0)), mode='edge')
        pts = np.stack([np.convolve(p[:, 0], k, 'valid'), np.convolve(p[:, 1], k, 'valid')], 1)
    seg = np.hypot(np.diff(pts[:, 0]) * sy, np.diff(pts[:, 1]) * sx)
    s = np.r_[0, np.cumsum(seg)]
    n = max(int(s[-1] // step_mm) + 1, 2)
    t = np.linspace(0, s[-1], n)
    return np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], 1)


def length_mm(pts, sx, sy):
    return float(np.hypot(np.diff(pts[:, 0]) * sy, np.diff(pts[:, 1]) * sx).sum())


def midline(myo, sx, sy):
    lab, n = ndimage.label(myo, structure=np.ones((3, 3)))
    if n == 0:
        return None
    if n > 1:
        myo = lab == (int(np.argmax(ndimage.sum(myo, lab, range(1, n + 1)))) + 1)
    return longest_path(skeletonize(myo), sx, sy)


def endo_border(lv, myo, sx, sy):
    """LV pixels 8-adjacent to the myocardium = endocardial border without the mitral plane"""
    b = lv & ndimage.binary_dilation(myo, structure=np.ones((3, 3)))
    return longest_path(skeletonize(b), sx, sy)


# ---------------------------------------------------------------- data
def to_tier(mask_orig, tier):
    m512 = np.array(Image.fromarray(mask_orig.astype(np.uint8) * 255).resize((512, 512), Image.NEAREST)) > 0
    return m512 if tier == 512 else m512[::512 // tier, ::512 // tier]


def model_mask(tag, i, tier):
    q = np.load(ECHO / 'results' / 'quant' / tag / 'outputs' / f'val_{i:03d}.npy')
    m512 = clean(np.repeat(np.repeat(q >= 1, 4, 0), 4, 1))
    return m512 if tier == 512 else m512[::512 // tier, ::512 // tier]


def load_sequence(p, view, tier):
    d = CAMUS / 'database_nifti' / p
    seq, sp = read_nifti(d / f'{p}_{view}_half_sequence.nii.gz')
    ed, _ = read_nifti(d / f'{p}_{view}_ED.nii.gz')
    es, _ = read_nifti(d / f'{p}_{view}_ES.nii.gz')
    if np.array_equal(seq[0], es) and np.array_equal(seq[-1], ed):
        seq = seq[::-1]                       # stored ES -> ED (18 TRAIN sequences, none in VAL)
    assert np.array_equal(seq[0], ed) and np.array_equal(seq[-1], es), (p, view, 'not ED -> ES')
    h, w = seq.shape[1:]
    k = 512 // tier
    frames = np.stack([host_stretch(f.astype(np.uint8))[::k, ::k] for f in seq])
    sx, sy = w * sp[0] / tier, h * sp[1] / tier          # mm per tier pixel (x, y)
    gt = {ph: read_nifti(d / f'{p}_{view}_{ph}_gt.nii.gz')[0] for ph in ('ED', 'ES')}
    return frames, sx, sy, gt


# ---------------------------------------------------------------- tracking
def track_line(frames, pts0, c):
    """pts0 (n, 2) (y, x) tier px -> (T, n, 2) tracked positions, edge-point count"""
    xq = np.round(pts0[:, 1] * 256).astype(np.int32)
    yq = np.round(pts0[:, 0] * 256).astype(np.int32)
    out = [np.stack([yq, xq], 1) / 256.0]
    edges, reg = 0, c['reg']
    for t in range(len(frames) - 1):
        x_old, y_old = xq.copy(), yq.copy()
        sad, fl = track_step(frames[t], frames[t + 1], xq, yq, c['R'], BM_OPT_VFIT if c['fit'] == 'v' else 0, c['k'])
        edges += int(((fl & 4) != 0).sum())
        if reg > 1:   # median of each point's displacement over its reg neighbours on the line
            dx, dy = (xq - x_old).astype(float), (yq - y_old).astype(float)
            pad = reg // 2
            win = np.lib.stride_tricks.sliding_window_view
            xq = (x_old + np.round(np.median(win(np.pad(dx, pad, mode='edge'), reg), 1))).astype(np.int32)
            yq = (y_old + np.round(np.median(win(np.pad(dy, pad, mode='edge'), reg), 1))).astype(np.int32)
        out.append(np.stack([yq, xq], 1) / 256.0)
    return np.stack(out), edges


def line_length(q, sx, sy, c):
    """length of a tracked line: drop `trim` points at each (basal) end, moving average over `smooth` points"""
    k = c['trim']
    q = q[k:len(q) - k] if k else q
    s = c['smooth']
    if s > 1:
        ker = np.ones(s) / s
        q = np.stack([np.convolve(q[:, 0], ker, 'valid'), np.convolve(q[:, 1], ker, 'valid')], 1)
    return length_mm(q, sx, sy)


def stats(pred, ref):
    pred, ref = np.asarray(pred, float), np.asarray(ref, float)
    d = pred - ref
    return dict(n=len(d), bias=float(d.mean()), mae=float(np.abs(d).mean()), sd=float(d.std(ddof=1)),
                r=float(np.corrcoef(pred, ref)[0, 1]), pred_mean=float(pred.mean()), ref_mean=float(ref.mean()))


def C(name, tier, R, fit, reg, smooth, trim=0, place='model', k=1):
    return dict(name=name, tier=tier, R=R, fit=fit, reg=reg, smooth=smooth, trim=trim, place=place, k=k)


# fit: 'p' parabola (bm_argmin default), 'v' equiangular; reg: displacement median window; smooth / trim: length;
# k: SAD tables of k line neighbours summed before the argmin (bm_track_line). FINAL = chosen on VAL (exp/agg.py,
# exp/anchor.py: anchoring the template to ED or every 4 frames is worse - speckle decorrelates)
FINAL = 'final_t512_r6_k9_v_s5'
CONFIGS = [
    C(FINAL, 512, 6, 'v', 1, 5, k=9), C('t256_r4_k5_v_s5', 256, 4, 'v', 1, 5, k=5),
    C('final_expertED', 512, 6, 'v', 1, 5, place='expert', k=9),
    C('t256_r4_p', 256, 4, 'p', 1, 1), C('t256_r4_v', 256, 4, 'v', 1, 1),
    C('t256_r4_v_med5_s5', 256, 4, 'v', 5, 5), C('t256_r4_v_med5_s5_trim2', 256, 4, 'v', 5, 5, 2),
    C('t256_r6_v_med5_s5', 256, 6, 'v', 5, 5), C('t256_r4_p_med5_s5', 256, 4, 'p', 5, 5), C('t256_r4_v_s5', 256, 4, 'v', 1, 5),
    C('t512_r8_p', 512, 8, 'p', 1, 1), C('t512_r8_v', 512, 8, 'v', 1, 1),
    C('t512_r8_v_med5_s5', 512, 8, 'v', 5, 5), C('t512_r8_p_med5_s5', 512, 8, 'p', 5, 5), C('t512_r8_v_s5', 512, 8, 'v', 1, 5), C('t512_r8_v_med5_s5_trim2', 512, 8, 'v', 5, 5, 2),
    C('t256_r4_v_med5_s5_expertED', 256, 4, 'v', 5, 5, 0, 'expert'),
    C('t512_r8_v_med5_s5_expertED', 512, 8, 'v', 5, 5, 0, 'expert'),
]
STEP_MM = 3.0


def main():
    t0 = time.time()
    meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    seqs = sorted({(m['patient'], m['view']) for m in meta})
    if len(sys.argv) > 1:
        seqs = seqs[:int(sys.argv[1])]          # smoke test
    per = []
    for (p, view) in seqs:
        rec = dict(patient=p, view=view, configs={})
        for tier in (256, 512):
            frames, sx, sy, gt = load_sequence(p, view, tier)
            rec['frames'] = len(frames)
            # references (expert ED / ES), at this tier
            ref = {}
            for ph in ('ED', 'ES'):
                lv, myo = to_tier(gt[ph] == LABELS['lv'], tier), to_tier(gt[ph] == LABELS['myo'], tier)
                ml, eb = midline(myo, sx, sy), endo_border(lv, myo, sx, sy)
                ref[ph] = dict(mid=length_mm(resample(ml, sx, sy, 1.0), sx, sy),
                               endo=length_mm(resample(eb, sx, sy, 1.0), sx, sy), midline=ml)
            rec[f'ref_mid_{tier}'] = ref['ES']['mid'] / ref['ED']['mid'] - 1
            rec[f'ref_endo_{tier}'] = ref['ES']['endo'] / ref['ED']['endo'] - 1
            rec[f'L_mid_ED_{tier}'] = ref['ED']['mid']
            # board-equivalent masks (programs 18 + F1) at ED / ES
            lv_ed, epi_ed = model_mask('lv_r128', mi[(p, view, 'ED')], tier), model_mask('epi_r128', mi[(p, view, 'ED')], tier)
            lv_es, epi_es = model_mask('lv_r128', mi[(p, view, 'ES')], tier), model_mask('epi_r128', mi[(p, view, 'ES')], tier)
            ml_ed, ml_es = midline(epi_ed & ~lv_ed, sx, sy), midline(epi_es & ~lv_es, sx, sy)
            rec[f'seg_mid_{tier}'] = (length_mm(resample(ml_es, sx, sy, 1.0), sx, sy)
                                      / length_mm(resample(ml_ed, sx, sy, 1.0), sx, sy) - 1)
            for c in CONFIGS:
                if c['tier'] != tier:
                    continue
                pts0 = resample(ml_ed if c['place'] == 'model' else ref['ED']['midline'], sx, sy, STEP_MM)
                trk, edges = track_line(frames, pts0, c)
                L = np.array([line_length(q, sx, sy, c) for q in trk])
                g = L / L[0] - 1
                rec['configs'][c['name']] = dict(curve=g.tolist(), es=float(g[-1]), peak=float(g.min()),
                                                 n_pts=len(pts0), edges=edges)
        per.append(rec)
        print(f"{p} {view} T={rec['frames']:2d} ref_mid {rec['ref_mid_256']*100:6.1f} "
              + ' '.join(f"{k} {v['es']*100:6.1f}" for k, v in rec['configs'].items()), flush=True)
    summ = summarise(per)
    summ['synthetic'] = synthetic(per)
    out = HERE / 'results'
    out.mkdir(exist_ok=True)
    (out / 'track_val.json').write_text(json.dumps(dict(configs=CONFIGS, summary=summ, per=per), indent=1, default=float),
                                        encoding='utf-8')
    for k, v in summ.items():
        if k != 'synthetic':
            s = v['vs_ref_mid']
            print(f"{k:32s} vs expert mid-wall: bias {s['bias']:6.2f} MAE {s['mae']:5.2f} r {s['r']:5.2f} "
                  f"(mean {s['pred_mean']:6.2f} vs {s['ref_mean']:6.2f})")
    for k, v in summ['synthetic'].items():
        print(f"synthetic {k:32s} strain err bias {v['strain_err_bias']:6.2f} MAE {v['strain_err_mae']:5.2f} "
              f"(exact {v['gt_mean']:6.2f}); point error P50 {v['pos_err_mm_p50']:.2f} mm, P95 {v['pos_err_mm_p95']:.2f} mm")
    print(f'done in {time.time() - t0:.0f} s')


def summarise(per):
    """summaries in % (strain x 100); GLS-like per patient = mean of 2CH and 4CH"""
    summ = {}
    for c in CONFIGS:
        name, tier = c['name'], c['tier']
        es = [r['configs'][name]['es'] * 100 for r in per]
        summ[name] = dict(vs_ref_mid=stats(es, [r[f'ref_mid_{tier}'] * 100 for r in per]),
                          vs_ref_endo=stats(es, [r[f'ref_endo_{tier}'] * 100 for r in per]),
                          n_pts_p50=float(np.median([r['configs'][name]['n_pts'] for r in per])),
                          edge_points=int(sum(r['configs'][name]['edges'] for r in per)))
        pts = {}
        for r in per:
            pts.setdefault(r['patient'], []).append((r['configs'][name]['es'] * 100, r[f'ref_mid_{tier}'] * 100,
                                                     r[f'ref_endo_{tier}'] * 100))
        both = [np.mean(v, 0) for v in pts.values() if len(v) == 2]
        if len(both) > 2:
            summ[name]['patient_vs_ref_mid'] = stats([b[0] for b in both], [b[1] for b in both])
            summ[name]['patient_vs_ref_endo'] = stats([b[0] for b in both], [b[2] for b in both])
    # FINAL: by image quality, and with the TRAIN-fitted linear calibration (calib_train.py), if present
    meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    qual = {(m['patient'], m['view']): m['quality'] for m in meta}
    tier = next(c for c in CONFIGS if c['name'] == FINAL)['tier']
    est = np.array([r['configs'][FINAL]['es'] * 100 for r in per])
    ref = np.array([r[f'ref_mid_{tier}'] * 100 for r in per])
    gm = np.array([qual[(r['patient'], r['view'])] != 'Poor' for r in per])
    summ[FINAL]['good_medium'] = stats(est[gm], ref[gm])
    summ[FINAL]['poor'] = stats(est[~gm], ref[~gm]) if (~gm).sum() > 2 else None
    cf = HERE / 'results' / 'calib_train.json'
    if cf.exists():
        cal = json.loads(cf.read_text(encoding='utf-8'))
        a, b = cal['a'], cal['b']
        e2 = a * est + b
        pat = {}
        for r, e, f in zip(per, e2, ref):
            pat.setdefault(r['patient'], []).append((e, f))
        both = np.array([np.mean(v, 0) for v in pat.values() if len(v) == 2])
        summ[FINAL + '_calibrated'] = dict(a=a, b=b, vs_ref_mid=stats(e2, ref), good_medium=stats(e2[gm], ref[gm]),
                                           patient_vs_ref_mid=stats(both[:, 0], both[:, 1]))
        # offset only (TRAIN mean bias): keeps the dynamic range; the linear fit (slope < 1, r ~ 0.55) mostly shrinks
        off = -cal['raw']['bias']
        e3 = est + off
        pat3 = {}
        for r, e, f in zip(per, e3, ref):
            pat3.setdefault(r['patient'], []).append((e, f))
        both3 = np.array([np.mean(v, 0) for v in pat3.values() if len(v) == 2])
        summ[FINAL + '_offset'] = dict(offset=off, vs_ref_mid=stats(e3, ref), good_medium=stats(e3[gm], ref[gm]),
                                       patient_vs_ref_mid=stats(both3[:, 0], both3[:, 1]))
    for tier in (256, 512):
        summ[f'segmentation_only_{tier}'] = dict(vs_ref_mid=stats([r[f'seg_mid_{tier}'] * 100 for r in per],
                                                                  [r[f'ref_mid_{tier}'] * 100 for r in per]))
        summ[f'ref_endo_{tier}'] = dict(vs_ref_mid=stats([r[f'ref_endo_{tier}'] * 100 for r in per],
                                                         [r[f'ref_mid_{tier}'] * 100 for r in per]))
    return summ


def synthetic(per, n_seq=20, T=16, target=-0.15):
    """ED frame warped by a known affine: x unchanged, y scaled about the apex (top of the mid-wall line) from 1 to
    1 + target over T frames (bilinear warp, no speckle decorrelation, no clutter -> optimistic). Exact strain =
    length change of the affinely mapped ED line (same line_length). Error stats at the last frame, model placement."""
    meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    res, seqs = {}, {}
    for c in CONFIGS:
        if c['place'] != 'model':
            continue
        errs, gts = [], []
        for r in per[:n_seq]:
            key = (r['patient'], r['view'], c['tier'])
            if key not in seqs:
                frames, sx, sy, _ = load_sequence(r['patient'], r['view'], c['tier'])
                lv = model_mask('lv_r128', mi[(r['patient'], r['view'], 'ED')], c['tier'])
                epi = model_mask('epi_r128', mi[(r['patient'], r['view'], 'ED')], c['tier'])
                pts0 = resample(midline(epi & ~lv, sx, sy), sx, sy, STEP_MM)
                y_apex = pts0[:, 0].min()
                ed = frames[0].astype(float)
                H, W = ed.shape
                yy, xx = np.mgrid[0:H, 0:W].astype(float)
                seq = [np.clip(np.round(ndimage.map_coordinates(
                    ed, [y_apex + (yy - y_apex) / (1 + target * t / (T - 1)), xx], order=1)), 0, 255).astype(np.uint8)
                    for t in range(T)]
                seqs[key] = (np.stack(seq), pts0, y_apex, sx, sy)
            seq, pts0, y_apex, sx, sy = seqs[key]
            trk, _ = track_line(seq, pts0, c)
            exact = pts0.copy()
            exact[:, 0] = y_apex + (pts0[:, 0] - y_apex) * (1 + target)
            gt = line_length(exact, sx, sy, c) / line_length(pts0, sx, sy, c) - 1
            est = line_length(trk[-1], sx, sy, c) / line_length(trk[0], sx, sy, c) - 1
            pos_err = np.hypot((trk[-1][:, 0] - exact[:, 0]) * sy, (trk[-1][:, 1] - exact[:, 1]) * sx)
            errs.append((est - gt) * 100)
            gts.append(dict(gt=gt * 100, p50=float(np.median(pos_err)), p95=float(np.percentile(pos_err, 95))))
        res[c['name']] = dict(n=len(errs), strain_err_bias=float(np.mean(errs)), strain_err_mae=float(np.mean(np.abs(errs))),
                              gt_mean=float(np.mean([g['gt'] for g in gts])),
                              pos_err_mm_p50=float(np.median([g['p50'] for g in gts])),
                              pos_err_mm_p95=float(np.median([g['p95'] for g in gts])))
    return res


if __name__ == '__main__':
    main()
