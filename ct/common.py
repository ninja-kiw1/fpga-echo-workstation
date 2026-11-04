"""⑤ CT spleen: shared helpers -- paths, NIfTI reading, HU windowing, the board's input chain, metrics.

Input chain (decided 10-10, see README):
  HU -> soft-tissue window W400 / L40 -> uint8 (512 x 512 slice, no resize: MSD slices are 512 x 512)
  -> host 4 x 4 box average with round-half-up ((sum + 8) >> 4) -> 128 x 128 payload of a compact frame
  -> board runs the model at 128 (no further sampling). Model index >= 7, so compact frames only.
"""
import gzip
import json
import struct
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
DATA = PLD / 'Task09_Spleen' / 'Task09_Spleen'
CACHE = ROOT / 'data' / 'cache'
RESULTS = ROOT / 'results'
WINDOW = (40.0, 400.0)                       # level, width (HU)
DTYPES = {2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
          256: np.int8, 512: np.uint16, 768: np.uint32}


def read_nifti(path):
    """-> (array [z, y, x] as stored, voxel size (dx, dy, dz) mm). Little-endian NIfTI-1, slope applied."""
    raw = gzip.decompress(Path(path).read_bytes())
    assert struct.unpack_from('<i', raw, 0)[0] == 348
    dim = struct.unpack_from('<8h', raw, 40)
    datatype = struct.unpack_from('<h', raw, 70)[0]
    pixdim = struct.unpack_from('<8f', raw, 76)
    vox_offset = int(struct.unpack_from('<f', raw, 108)[0])
    slope, inter = struct.unpack_from('<2f', raw, 112)
    shape = tuple(int(d) for d in dim[1:1 + dim[0]])
    a = np.frombuffer(raw, DTYPES[datatype], int(np.prod(shape)), vox_offset).reshape(shape[::-1])
    if slope not in (0.0, 1.0) or inter != 0.0:
        a = a * slope + inter
    return a, tuple(float(p) for p in pixdim[1:4])


def window_u8(hu, level=WINDOW[0], width=WINDOW[1]):
    lo = level - width / 2
    return np.clip(np.rint((np.asarray(hu, np.float32) - lo) * (255.0 / width)), 0, 255).astype(np.uint8)


def box(u8, k=4):
    """[..., H, W] uint8 -> [..., H/k, W/k] uint8: k x k mean, round half up (exact integer op).
    512 -> 128 with k = 4 (coarse payload), 256 crop -> 128 with k = 2 (stage-2 ROI payload)."""
    if k == 1:
        return np.asarray(u8, np.uint8)
    h, w = np.shape(u8)[-2:]
    s = np.asarray(u8).reshape(*np.shape(u8)[:-2], h // k, k, w // k, k).astype(np.uint32).sum(axis=(-3, -1))
    return ((s + k * k // 2) // (k * k)).astype(np.uint8)


def neighbour_offset(dz_mm, target_mm=5.0):
    """slice offset for the neighbour channels: the slice closest to +-5 mm, at least 1."""
    return max(1, int(round(target_mm / dz_mm)))


def neighbours(nz, dz_mm):
    """(prev, next) slice index per slice, clamped at the volume ends."""
    o = neighbour_offset(dz_mm)
    z = np.arange(nz)
    return np.clip(z - o, 0, nz - 1), np.clip(z + o, 0, nz - 1)


def stack_input(img, mode, dz_mm):
    """[z, H, W] uint8 slices -> [z, H, W, 3] board input.
    rep: R = G = B = slice z;  z3: R = slice z - o, G = slice z, B = slice z + o (o ~ 5 mm)."""
    if mode == 'rep':
        return np.repeat(img[..., None], 3, axis=-1)
    p, n = neighbours(len(img), dz_mm)
    return np.stack([img[p], img, img[n]], axis=-1)


def load_split():
    return json.loads((RESULTS / 'split.json').read_text(encoding='utf-8'))


def load_case(case):
    """cached case -> dict(img=[z,512,512] uint8 windowed, lab=[z,512,512] uint8, spacing)."""
    z = np.load(CACHE / f'{case}.npz')
    return dict(img=z['img'], lab=z['lab'], spacing=tuple(float(v) for v in z['spacing']))


def upsample_bilinear(q, k=4):
    """[z, n, n] integer logits -> [z, n*k, n*k] float32, bilinear with half-pixel centres and edge clamping
    (= torch interpolate(align_corners=False)). For k = 4 every weight is a multiple of 1/8, so the result
    is exact in float32 whatever the order of operations: the host UI (tcp_image_ui/ct_volume.py, no torch)
    gets bit-identical masks."""
    q = np.asarray(q, np.float32)
    n = q.shape[-1]
    src = np.maximum((np.arange(n * k, dtype=np.float64) + 0.5) / k - 0.5, 0.0)
    i0 = np.floor(src).astype(np.int64)
    i1 = np.minimum(i0 + 1, n - 1)
    w1 = (src - i0).astype(np.float32)
    w0 = np.float32(1) - w1
    rows = q[:, i0, :] * w0[None, :, None] + q[:, i1, :] * w1[None, :, None]
    return rows[:, :, i0] * w0[None, None, :] + rows[:, :, i1] * w1[None, None, :]


def dice3d(pred, gt):
    p, g = np.asarray(pred, bool), np.asarray(gt, bool)
    s = int(p.sum()) + int(g.sum())
    return 1.0 if s == 0 else 2.0 * int((p & g).sum()) / s


def largest_cc3d(mask):
    """largest 26-connected component (empty stays empty)."""
    from scipy import ndimage
    lab, n = ndimage.label(mask, structure=np.ones((3, 3, 3), bool))
    if n <= 1:
        return mask.astype(bool)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    return lab == int(np.argmax(sizes))
