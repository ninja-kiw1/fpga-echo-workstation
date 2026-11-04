"""⑤ stage 2 (ROI fine segmentation): the deterministic two-stage pipeline, numpy only.

  coarse_mask   board coarse logits (model 14, 128 grid of the whole slice) -> bilinear x4 + largest 3D component
  roi_window    one 256 x 256 window per volume, centred on the in-plane bounding box of the coarse mask
                (clamped inside the image), slice range = coarse range +- 2 neighbour offsets (~10 mm)
  fine_inputs   per slice of the range: the window cropped, 2 x 2 box average -> 128 x 128 x 3 payload
                  roi_z3    R / G / B = window of slices z-o / z / z+o
                  roi_prior R = window of slice z, G = coarse mask in the window (2 x 2 mean >= 0.5 -> 255),
                            B = window of (slice z-o + slice z+o + 1) >> 1
  combine       fine scores -> bilinear x2 into the window -> threshold -> pasted into an empty volume -> largest 3D component
Host UI copy: tcp_image_ui/ct_volume.py (checked identical by verify_board_roi.py).
"""
import numpy as np

from common import largest_cc3d, neighbour_offset, neighbours, box, upsample_bilinear

CROP = 256
KF = 2                                  # 256 crop -> 128 payload (2 x 2 box average)
Z_MARGIN = 2                            # neighbour offsets added above and below the coarse slice range


def coarse_mask(q0):
    """[z, 128, 128] int8 -> [z, 512, 512] bool (the single-stage result: bilinear + largest component)."""
    m = np.empty((len(q0), 512, 512), bool)
    for i in range(0, len(q0), 32):
        m[i:i + 32] = upsample_bilinear(q0[i:i + 32], 4) > 0.5
    return largest_cc3d(m)


def roi_window(m0, dz_mm):
    """-> (y0, x0, z0, z1) or None when the coarse mask is empty."""
    zs = np.nonzero(m0.reshape(len(m0), -1).any(1))[0]
    if len(zs) == 0:
        return None
    r, c = np.nonzero(m0.any(0))
    cy, cx = (int(r.min()) + int(r.max()) + 1) // 2, (int(c.min()) + int(c.max()) + 1) // 2
    y0 = min(max(cy - CROP // 2, 0), 512 - CROP)
    x0 = min(max(cx - CROP // 2, 0), 512 - CROP)
    o = neighbour_offset(dz_mm)
    return y0, x0, max(0, int(zs.min()) - Z_MARGIN * o), min(len(m0) - 1, int(zs.max()) + Z_MARGIN * o)


def fine_inputs(img, m0, win, mode, dz_mm):
    """img [z, 512, 512] uint8, m0 coarse mask -> [z1 - z0 + 1, 128, 128, 3] uint8 payloads."""
    y0, x0, z0, z1 = win
    p, n = neighbours(len(img), dz_mm)
    zz = np.arange(z0, z1 + 1)
    crop = lambda a: a[..., y0:y0 + CROP, x0:x0 + CROP]
    if mode == 'roi_z3':
        ch = [box(crop(img[p[zz]]), KF), box(crop(img[zz]), KF), box(crop(img[n[zz]]), KF)]
    elif mode == 'roi_prior':
        prior = crop(m0[zz]).reshape(len(zz), CROP // 2, 2, CROP // 2, 2).sum(axis=(2, 4)) >= 2
        nb = (crop(img[p[zz]]).astype(np.uint16) + crop(img[n[zz]]) + 1) >> 1
        ch = [box(crop(img[zz]), KF), prior.astype(np.uint8) * 255, box(nb.astype(np.uint8), KF)]
    else:
        raise ValueError(mode)
    return np.ascontiguousarray(np.stack(ch, axis=-1))


def combine(score, win, nz, thr):
    """score [z1 - z0 + 1, 128, 128] (int8 logits with thr 0.5, or float logits with thr 0) -> [nz, 512, 512] bool."""
    y0, x0, z0, z1 = win
    m = np.zeros((nz, 512, 512), bool)
    m[z0:z1 + 1, y0:y0 + CROP, x0:x0 + CROP] = upsample_bilinear(score, KF) > thr
    return largest_cc3d(m)


def two_stage(q0, img, dz_mm, mode, run_fine, thr=0.5):
    """full pipeline. run_fine: [m, 128, 128, 3] uint8 -> [m, 128, 128] scores. -> (mask, window or None, payloads)."""
    m0 = coarse_mask(q0)
    win = roi_window(m0, dz_mm)
    if win is None:
        return m0, None, None
    x = fine_inputs(img, m0, win, mode, dz_mm)
    return combine(run_fine(x), win, len(img), thr), win, x
