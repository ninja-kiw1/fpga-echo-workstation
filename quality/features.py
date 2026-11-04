"""Innovation ② step 2: zero-extra-model quality features from what the board already computes (the LV program's
integer logits, optionally the LA program's, and the grey frames themselves), per frame and per sequence.

Per frame (128 tier; LV = clean(q >= 1), the board decision; sector = largest component of temporal max > 4, holes
filled, i.e. the sector auto-detect of the live mode):
  touch      share of the LV border pixels within 2 px of the sector edge          (LV cut off by the sector)
  apex_gap   (LV top row - sector top row) / sector height                          (foreshortening: apex too deep)
  length     LV long-axis extent / sector height                                    (foreshortening / depth)
  round      LV short / long axis extent                                            (foreshortened LV looks round)
  unc        share of the +-2 px band around the LV border with |q| <= 2            (the network is unsure)
  multi      more than one component before clean()                                (fragmented output)
  contrast   (ring - cavity) / (ring + cavity + 1) grey means, cavity = LV eroded 2, ring = 2..6 px outside
  la_ok      LA program present (>= 10 % of the LV area) and below the LV centroid (only where LA logits exist)
Per sequence: means of the above over the frames used + stability = mean Dice of consecutive LV masks,
roughness = mean |second difference of the LV area| / mean area.

`frames` selects which frames enter the means: CAMUS TRAIN uses the in-between frames only (the F1 model was trained
on TRAIN ED / ES frames, which would look too confident); VAL uses the same in-between frames for consistency.
"""
import numpy as np
from scipy import ndimage

FEATURES = ['touch', 'apex_gap', 'length', 'round', 'unc', 'multi', 'contrast', 'stability', 'roughness']


def sector_mask(frames):
    m = frames.max(0) > 4
    lab, n = ndimage.label(m)
    if n > 1:
        m = lab == (int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1)
    return ndimage.binary_fill_holes(m)


def clean(m):
    lab, n = ndimage.label(m)
    if n > 1:
        m = lab == (int(np.argmax(ndimage.sum(m, lab, range(1, n + 1)))) + 1)
    return ndimage.binary_fill_holes(m), n


def frame_features(img, q, sector, edge_band, sec_top, sec_h, q_la=None):
    m, n = clean(q >= 1)
    f = dict(multi=float(n > 1))
    if m.sum() < 10:
        f.update(touch=1.0, apex_gap=1.0, length=0.0, round=1.0, unc=1.0, contrast=0.0, la_ok=0.0, empty=1.0)
        return f, m
    border = m & ~ndimage.binary_erosion(m)
    f['touch'] = float((border & edge_band).sum() / max(border.sum(), 1))
    r, c = np.nonzero(m)
    f['apex_gap'] = float((r.min() - sec_top) / sec_h)
    p = np.stack([r, c], 1).astype(float)
    p -= p.mean(0)
    w, v = np.linalg.eigh(p.T @ p)
    s1, s2 = p @ v[:, 1], p @ v[:, 0]
    L, Wd = s1.max() - s1.min() + 1, s2.max() - s2.min() + 1
    f['length'] = float(L / sec_h)
    f['round'] = float(Wd / L)
    band = ndimage.binary_dilation(m, iterations=2) & ~ndimage.binary_erosion(m, iterations=2)
    f['unc'] = float((np.abs(q[band].astype(int)) <= 2).mean())
    cav = ndimage.binary_erosion(m, iterations=2)
    ring = ndimage.binary_dilation(m, iterations=6) & ~ndimage.binary_dilation(m, iterations=2) & sector
    a, b = (img[cav].mean() if cav.any() else 0.0), (img[ring].mean() if ring.any() else 0.0)
    f['contrast'] = float((b - a) / (a + b + 1))
    if q_la is not None:
        la, _ = clean(q_la >= 1)
        f['la_ok'] = float(la.sum() >= 0.1 * m.sum() and (np.nonzero(la)[0].mean() > r.mean() if la.any() else False))
    f['empty'] = 0.0
    return f, m


def sequence_features(frames, q, use, q_la=None):
    """frames uint8 [T, H, W], q int8 logits [T, H, W], use = indices of the frames that enter the means"""
    sector = sector_mask(frames)
    edge_band = sector & ~ndimage.binary_erosion(sector, iterations=2)
    rows = np.nonzero(sector.any(1))[0]
    sec_top, sec_h = rows.min(), rows.max() - rows.min() + 1
    per, masks = [], []
    for t in range(len(frames)):
        f, m = frame_features(frames[t], q[t], sector, edge_band, sec_top, sec_h, None if q_la is None else q_la[t])
        per.append(f)
        masks.append(m)
    out = {k: float(np.mean([per[t][k] for t in use])) for k in per[0]}
    d = []
    for t in range(1, len(masks)):
        s = masks[t].sum() + masks[t - 1].sum()
        d.append(2 * (masks[t] & masks[t - 1]).sum() / s if s else 1.0)
    out['stability'] = float(np.mean(d)) if d else 1.0
    area = np.array([m.sum() for m in masks], float)
    out['roughness'] = float(np.abs(np.diff(area, 2)).mean() / max(area.mean(), 1)) if len(area) > 2 else 0.0
    return out, per
