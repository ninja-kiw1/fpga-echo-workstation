"""ef_simpson.py —— F4：双平面 Simpson 圆盘法算左心室容积和射血分数（上位机与离线评测共用这一份）。

F4: LV volumes and ejection fraction by Simpson's biplane method of disks.

Re-implementation of the official CAMUS notebook (database "jupyter/script_camus_ef.ipynb",
Leclerc et al., IEEE TMI 2019, doi 10.1109/TMI.2019.2900516; CC BY-NC-SA 4.0), kept
step for step so that expert masks reproduce the reference EF given in Info_*.cfg:
  * resample to isotropic pixels (NEAREST on the coarser axis);
  * first contour of skimage.measure.find_contours(mask, 0.5);
  * mitral (AV) plane = longest chord between contour points i > j whose direction is
    within 45 deg of horizontal and with every contour point between them within 8 px of it;
  * apex = contour point farthest from the chord's middle contour point;
  * 20 disks along midpoint -> apex, diameter = edge-to-edge distance along the normal,
    each side marched from 8 px in, in 0.5 px steps;
  * volume = sum(d_a2c * d_a4c) * step * pi / 4 with the largest of the four step sizes,
    rounded to whole millilitres; EF = round(100 * (EDV - ESV) / EDV).
The notebook itself warns that implementation details change the numbers; only the
reproduction rate on expert masks (check_ef_gt.py) says this port is faithful.
Predicted masks go through clean() first (largest component, holes filled) -- the one
addition to the notebook, on the PC.

Needs numpy + scipy + scikit-image + Pillow (the srtp-vision environment on this PC);
the UI disables its EF panel when they are missing. echo/ef.py re-exports this module.
"""
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage.measure import find_contours


def clean(mask):
    """Largest 4-connected component with its holes filled. The notebook takes the first
    contour only, so a stray blob or a hole in a predicted mask would be measured instead
    of the cavity (validation: >1 component in 21 % of F1 frames). Expert masks: no-op."""
    lab, n = ndimage.label(mask)
    if n > 1:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        mask = lab == (int(np.argmax(sizes)) + 1)
    return ndimage.binary_fill_holes(mask)


def to_isotropic(mask, spacing):
    """mask (H, W) bool, spacing (height, width) mm -> (mask', isotropic spacing)."""
    scaling = np.array(spacing) / min(spacing)
    h, w = (np.array(mask.shape) * scaling).round().astype(int)
    m = np.array(Image.fromarray(mask.astype(np.uint8)).resize((w, h), Image.NEAREST))
    return m, min(spacing)


def _distance_to_edge(seg, point, normal):
    d = 8.0                                         # start a bit in, as the notebook
    while True:
        y, x = np.round(point + d * normal).astype(int)
        if not (0 <= y < seg.shape[0] and 0 <= x < seg.shape[1]) or seg[y, x] == 0:
            return d
        d += 0.5


def _line_to_points(p0, p1, pts):
    v = p1 - p0
    w = p0 - pts
    return np.abs(v[0] * w[:, 1] - v[1] * w[:, 0]) / np.linalg.norm(v)


def diameters(mask, spacing):
    """-> (20 disk diameters in mm, disk step in mm, geometry dict for display)."""
    seg, iso = to_isotropic(mask, spacing)
    contour = find_contours(seg, 0.5)[0]
    best, bi, bj = 0.0, None, None
    for i in range(2, len(contour)):
        diff = contour[:i] - contour[i]
        ang = np.abs(np.degrees(np.arctan2(diff[:, 0], diff[:, 1])))
        for j in np.nonzero(ang <= 45)[0]:
            if np.all(_line_to_points(contour[i], contour[j], contour[j + 1:i]) <= 8):
                dist = np.linalg.norm(contour[i] - contour[j])
                if best < dist:
                    best, bi, bj = dist, i, j
    mid = int(bj + round((bi - bj) / 2))
    lengths = np.linalg.norm(contour - contour[mid], axis=1)
    apex = int(np.argmax(lengths))                  # first maximum, as the notebook's strict '<' loop
    axis_len = lengths[apex]
    direction = contour[apex] - contour[mid]
    normal = np.array([-direction[1], direction[0]])
    normal = normal / np.linalg.norm(normal)
    d, ends = [], []
    for f in np.linspace(0, 1, 20, endpoint=False):
        p = contour[mid] + direction * f
        d1, d2 = _distance_to_edge(seg, p, normal), _distance_to_edge(seg, p, -normal)
        d.append((d1 + d2) * iso)
        ends.append((p + d1 * normal, p - d2 * normal))
    # geometry in ORIGINAL pixel coordinates (row, col), for drawing
    back = iso / np.array(spacing, dtype=float)
    geom = dict(base=(contour[bj] * back, contour[bi] * back), mid=contour[mid] * back,
                apex=contour[apex] * back, disks=[(a * back, b * back) for a, b in ends], contour=contour * back)
    return np.array(d), axis_len * iso / 20, geom


def volumes(a2c_ed, a2c_es, a2c_spacing, a4c_ed, a4c_es, a4c_spacing):
    """Binary LV masks at the original resolution, spacing (height, width) mm -> (EDV, ESV) ml."""
    d2e, s2e, _ = diameters(a2c_ed, a2c_spacing)
    d2s, s2s, _ = diameters(a2c_es, a2c_spacing)
    d4e, s4e, _ = diameters(a4c_ed, a4c_spacing)
    d4s, s4s, _ = diameters(a4c_es, a4c_spacing)
    step = max(s2e, s2s, s4e, s4s)
    vol = lambda a, b: int(round(float(np.sum((a / 1000) * (b / 1000)) * (step / 1000) * np.pi / 4 * 1e6)))
    return vol(d2e, d4e), vol(d2s, d4s)


def ejection_fraction(edv, esv):
    return int(round(100 * (edv - esv) / edv)) if edv else None
