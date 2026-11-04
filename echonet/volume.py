"""③ single-plane LV volume from a mask (pixel units; EchoNet gives no pixel size, EF is scale free).

volume_pca  method of disks along the mask's principal axis: 20 disks between the two ends of the axis, each disk's
            diameter = the mask area inside that slab / slab thickness (an "equivalent width", robust to jagged
            edges of a 112 x 112 mask); V = sum(pi / 4 * w^2 * thickness). No mitral-plane detection needed.
clean       largest 4-connected component, holes filled (as F4's ef_simpson.clean).
"""
import numpy as np
from scipy import ndimage

NDISK = 20


def clean(mask):
    lab, n = ndimage.label(mask)
    if n > 1:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        mask = lab == (int(np.argmax(sizes)) + 1)
    return ndimage.binary_fill_holes(mask)


def volume_pca(mask, return_axis=False):
    r, c = np.nonzero(mask)
    if len(r) < 3:
        return (0.0, None) if return_axis else 0.0
    p = np.stack([r, c], 1).astype(np.float64)
    mu = p.mean(0)
    q = p - mu
    w, v = np.linalg.eigh(q.T @ q)
    u = v[:, 1]                                    # principal (long) axis
    s = q @ u
    lo, hi = s.min() - 0.5, s.max() + 0.5
    h = (hi - lo) / NDISK
    k = np.minimum(((s - lo) / h).astype(int), NDISK - 1)
    width = np.bincount(k, minlength=NDISK) / h    # pixels in the slab / slab thickness
    vol = float(np.sum(np.pi / 4 * width ** 2 * h))
    if return_axis:
        return vol, dict(centre=mu, axis=u, lo=lo, hi=hi)
    return vol


def ef(v_ed, v_es):
    return 100.0 * (v_ed - v_es) / v_ed if v_ed > 0 else float('nan')
