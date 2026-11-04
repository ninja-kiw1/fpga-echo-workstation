"""③ EchoNet-Dynamic real-time echo workstation: shared helpers (paths, video decoding, masks, board input chain).

Board input chain (decided 10-10, README):
  AVI frame (112 x 112, 3 nearly equal channels) -> grey = (B + G + R + 1) // 3 (integer)
  -> zero padding 8 px on every side -> 128 x 128 compact grey frame -> board runs the model at 128
  -> logits cropped back to [8:120, 8:120] = the native 112 x 112 grid (no resampling anywhere).
Labels: the official polygon of VolumeTracings (long axis row dropped, chord ends joined), skimage.draw.polygon on the
112 grid, exactly as echonet.datasets.Echo does; ED / ES = the traced frame with the larger / smaller area.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
DATA = PLD / 'EchoNet-Dynamic'
CACHE = ROOT / 'data' / 'cache'
RESULTS = ROOT / 'results'
PAD = 8
N = 112


def grey(frame_bgr):
    f = np.asarray(frame_bgr, np.uint16)
    return ((f[..., 0] + f[..., 1] + f[..., 2] + 1) // 3).astype(np.uint8)


def read_video(path):
    """-> [T, 112, 112] uint8 grey, fps (from the container)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    out = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        out.append(grey(fr))
    cap.release()
    return np.stack(out) if out else np.zeros((0, N, N), np.uint8), fps


def temporal_offset(fps, target_s=0.04):
    """frame offset for the temporal channels (t3 encoding): the frame closest to 40 ms away, at least 1."""
    return max(1, int(round(target_s * fps)))


def pad128(x):
    """[..., 112, 112] -> [..., 128, 128] with zeros (the sector background is black)."""
    return np.pad(np.asarray(x), [(0, 0)] * (np.ndim(x) - 2) + [(PAD, PAD), (PAD, PAD)])


def crop112(x):
    return np.asarray(x)[..., PAD:PAD + N, PAD:PAD + N]


def file_list():
    with open(DATA / 'FileList.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r['name'] = r['FileName'] if r['FileName'].endswith('.avi') else r['FileName'] + '.avi'
    return rows


def tracings():
    """{video: {frame: [[x1, y1, x2, y2], ...] in CSV order}} with the frame order of the CSV kept."""
    tr = defaultdict(dict)
    with open(DATA / 'VolumeTracings.csv', newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            tr[r['FileName']].setdefault(int(r['Frame']), []).append(
                [float(r['X1']), float(r['Y1']), float(r['X2']), float(r['Y2'])])
    return tr


def trace_mask(rows):
    """official polygon -> [112, 112] bool. Only the first 21 rows (long axis + 20 chords) are used."""
    from skimage.draw import polygon
    t = np.array(rows[:21])
    x = np.concatenate((t[1:, 0], np.flip(t[1:, 2])))
    y = np.concatenate((t[1:, 1], np.flip(t[1:, 3])))
    r, c = polygon(np.rint(y).astype(int), np.rint(x).astype(int), (N, N))
    m = np.zeros((N, N), bool)
    m[r, c] = True
    return m


def disk_volume(rows):
    """single-plane method of disks straight from the tracing: 20 chords perpendicular to the long axis,
    V = sum(pi / 4 * d^2) * L / 20 (pixel units; EchoNet gives no pixel size, EF is scale free)."""
    t = np.array(rows[:21])
    L = float(np.hypot(t[0, 2] - t[0, 0], t[0, 3] - t[0, 1]))
    d = np.hypot(t[1:, 2] - t[1:, 0], t[1:, 3] - t[1:, 1])
    return float(np.sum(np.pi / 4 * d ** 2) * L / 20)


def load_frames(split):
    x = np.load(CACHE / f'{split}_img112.npy', mmap_mode='r')
    y = np.load(CACHE / f'{split}_lab112.npy', mmap_mode='r')
    meta = json.loads((CACHE / f'{split}_meta.json').read_text(encoding='utf-8'))
    return x, y, meta


def load_videos(split):
    """whole videos: frames memmap [T_total, 112, 112] + index {video: dict(start, n, fps, ef, ...)}."""
    x = np.load(CACHE / f'{split}_videos.npy', mmap_mode='r')
    idx = json.loads((CACHE / f'{split}_videos.json').read_text(encoding='utf-8'))
    return x, idx


def dice(a, b):
    s = int(a.sum()) + int(b.sum())
    return 1.0 if s == 0 else 2.0 * int((a & b).sum()) / s
