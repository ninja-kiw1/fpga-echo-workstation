"""③ per-frame operations shared by the offline scripts and the UI-code check (numpy + scipy only, no torch):
video_inputs (board payloads of a whole video) and features (per-frame QC info + volume from a logit map)."""
import numpy as np
from scipy import ndimage

from common import PAD, temporal_offset
from volume import clean, volume_pca


def video_inputs(frames, mode, fps):
    """[n, 112, 112] -> [n, 3, 128, 128] uint8 payloads (rep / t3)."""
    n = len(frames)
    if mode == 't3':
        d = temporal_offset(fps)
        z = np.arange(n)
        x = np.stack([frames[np.clip(z - d, 0, n - 1)], frames, frames[np.clip(z + d, 0, n - 1)]], 1)
    else:
        x = np.repeat(frames[:, None], 3, 1)
    return np.pad(x, ((0, 0), (0, 0), (PAD, PAD), (PAD, PAD)))


def features(logit):
    """logit [112, 112] float (FP32 units) -> dict of per-frame features + mask."""
    m = logit > 0
    lab, ncomp = ndimage.label(m)
    mc = clean(m) if ncomp else m
    band = ndimage.binary_dilation(mc, iterations=3) if mc.any() else np.zeros_like(mc)
    unc = float((np.abs(logit[band]) < 1.0).mean()) if band.any() else 1.0
    border = bool(mc[0].any() or mc[-1].any() or mc[:, 0].any() or mc[:, -1].any())
    return mc, (int(m.sum()), int(ncomp), int(mc.sum()), volume_pca(mc), unc, border)
