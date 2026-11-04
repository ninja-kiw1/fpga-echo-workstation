"""v5.26 board live source for the CAMUS quality chain: the 10-31 simulated scan (scan_sim/results/stream.npz, CAMUS VAL,
4 segments chosen by the pre-set median-score rule: 2CH Good -> 4CH Poor -> 4CH Medium -> 4CH Good, 50 fps, 500 frames)
as a 640 x 480 "screen recording" .y8 like live/make_y8.py: the 128 frame nearest-zoomed x 3 to 384 at (128, 48), so the
board's fixed crop 128 48 384 -> lc_crop_resize_n(128) gives back the CAMUS frame bit for bit (the F1 input of the
offline validation). Static attribution text outside the crop. Registered in live/src/index.json with
"quality": "camus" (the PC sends the CAMUS chain only for such sources).
CAMUS: Leclerc et al. 2019, CC BY-NC-SA 4.0 (non-commercial) -- the file stays on this PC and on the board.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I make_camus_src.py
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(PLD / 'live'))
from make_y8 import write_y8  # noqa: E402

NAME = 'CAMUS_scan_demo_z3.y8'
W, H, Z, X, Y = 640, 480, 3, 128, 48
SRC = PLD / 'live' / 'src'


def main():
    z = np.load(PLD / 'quality' / 'scan_sim' / 'results' / 'stream.npz')
    meta = json.loads((PLD / 'quality' / 'scan_sim' / 'results' / 'stream.json').read_text(encoding='utf-8'))
    fr = z['frames']
    base = Image.new('L', (W, H), 0)
    d = ImageDraw.Draw(base)
    d.text((8, 8), 'CAMUS VAL (Leclerc 2019, CC BY-NC-SA 4.0) - simulated scan', fill=140)
    d.text((8, H - 20), 'non-commercial demo source', fill=140)
    screen = np.asarray(base, np.uint8)
    out = np.repeat(screen[None], len(fr), 0)
    out[:, Y:Y + 128 * Z, X:X + 128 * Z] = np.kron(fr, np.ones((1, Z, Z), np.uint8))
    write_y8(str(SRC / NAME), out, meta['fps'])
    idx = json.loads((SRC / 'index.json').read_text(encoding='utf-8'))
    idx[NAME] = dict(video='CAMUS VAL simulated scan (quality/scan_sim)', fps=meta['fps'], frames=len(fr), W=W, H=H, zoom=float(Z),
                     x=X, y=Y, side=128 * Z, quality='camus',
                     segments=[{k: s[k] for k in ('patient', 'view', 'quality', 'start', 'n')} for s in meta['segments']])
    (SRC / 'index.json').write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding='utf-8')
    print(NAME, out.shape, (SRC / NAME).stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
