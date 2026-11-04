"""⑤ CT spleen: figures for the README / report from the integer outputs (= board outputs) of the validation cases.

  results/eval/<tag>/val_panels.png   per case (sorted by Dice): axial slice with the largest spleen area +
                                      coronal reformat through the spleen; green = label, red = board result
                                      after the chosen post-processing
  results/eval/<tag>/val_render.png   3D surface of board result vs label for the median case (tcp_image_ui renderer)

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I make_figures_ct.py --tag z3_r128
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402
from PIL import Image, ImageDraw, ImageFont   # noqa: E402

import ct_volume            # noqa: E402
from common import RESULTS, load_split   # noqa: E402
from train_ct import load_set   # noqa: E402

GREEN, RED = (64, 230, 96), (255, 64, 64)


def overlay(gray, gt, pred):
    rgb = np.repeat(gray[..., None], 3, -1)
    rgb[ct_volume.contour(gt)] = GREEN
    e = ct_volume.contour(pred)
    rgb[e | np.roll(e, 1, 1)] = RED
    return rgb


def font(size):
    for f in ('msyh.ttc', 'simhei.ttf', 'arial.ttf'):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    ev = json.loads((RESULTS / 'eval' / args.tag / 'val.json').read_text(encoding='utf-8'))
    chosen = ev['chosen']
    va = load_set(load_split()['val'])
    order = sorted(range(len(va['cases'])), key=lambda i: ev['per_case'][i][chosen]['dice'])
    tiles, f = [], font(18)
    finals = {}
    for i in order:
        c = va['cases'][i]
        a, b = va['spans'][i]
        img, gt = va['img'][a:b], va['lab'][a:b].astype(bool)
        q = np.load(RESULTS / 'quant' / args.tag / 'outputs' / f'{c}.npy')
        pred = ct_volume.postprocess(q, chosen)
        finals[c] = pred
        z = int(np.argmax(gt.reshape(len(gt), -1).sum(1)))
        ax = overlay(img[z], gt[z], pred[z])
        rc = int(np.nonzero(gt.any(axis=(0, 2)))[0].mean())
        sp = va['spacing'][i]
        h = int(round(len(img) * sp[2] / sp[0]))
        cor_g = np.array(Image.fromarray(img[::-1, rc, :]).resize((512, h), Image.BILINEAR))
        rs = lambda m: np.array(Image.fromarray(m.astype(np.uint8) * 255).resize((512, h), Image.NEAREST)) > 0
        cor = overlay(cor_g, rs(gt[::-1, rc, :]), rs(pred[::-1, rc, :]))
        cor = np.array(Image.fromarray(cor).resize((512, min(h, 512)), Image.BILINEAR)) if h > 512 else cor
        col = np.full((512 + 512 + 40, 512, 3), 255, np.uint8)
        col[40:552] = ax
        col[552:552 + cor.shape[0]] = cor
        im = Image.fromarray(col)
        r = ev['per_case'][i][chosen]
        ImageDraw.Draw(im).text((6, 8), '%s  Dice %.3f  体积 %+.1f%%  HD95 %.1f mm' % (c, r['dice'], r['vol_err_pct'], r['hd95_mm']),
                                fill=(0, 0, 0), font=f)
        tiles.append(np.array(im.resize((384, int(im.height * 384 / 512)), Image.BILINEAR)))
    panel = np.concatenate(tiles, 1)
    out = RESULTS / 'eval' / args.tag
    Image.fromarray(panel).save(out / 'val_panels.png')
    mid = va['cases'][order[len(order) // 2]]
    i = va['cases'].index(mid)
    a, b = va['spans'][i]
    sp = va['spacing'][i]
    gt = va['lab'][a:b].astype(bool)
    r = [ct_volume.Surface(m, sp).render(ang, 320, col) for m, col in ((finals[mid], (214, 92, 74)), (gt, (80, 190, 110)))
         for ang in (0, 60)]
    Image.fromarray(np.concatenate(r, 1)).save(out / 'val_render.png')
    print('wrote', out / 'val_panels.png', out / 'val_render.png', 'order', [va['cases'][i] for i in order])


if __name__ == '__main__':
    main()
