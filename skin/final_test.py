"""ONE-TIME evaluation on the 379 official ISIC 2016 Task 1 test images.

Everything (architecture, resolution tier, checkpoint, calibration, INT16
nodes, threshold p > 0.5) is frozen before this runs. The script refuses to
run twice unless --force, so the test set cannot drift into model selection.

Reports FP32, all-INT8 and the deployed mixed-precision model, all through the
deployment chain (host LANCZOS 512x512 -> board stride subsample -> board
nearest upsample -> PIL NEAREST to original size), with ISIC 2016 metrics at
the original resolution. With --sim (default on) the deployed model's output
on every test image is also recomputed by the HLS bit-level simulator and
must match the integer reference exactly.

    python final_test.py --tag r128
"""
import argparse
import datetime
import json
import sys

import numpy as np
import torch
from PIL import Image

from common import RESULTS, ROOT, board_upsample, isic_metrics, load_cells, load_split, mean_metrics
from export_verify import SizedRunner
from int8_reference import run_integer
from models.get_model import get_arch


def load_plan(qdir, prefix):
    plan = json.loads((qdir / f'{prefix}quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / f'{prefix}parameters.npz', allow_pickle=False) as z:
        return plan, {k: z[k] for k in z.files}


def panel(rgb512, gt512, pred512, title):
    """RGB | GT contour-ish overlay | prediction overlay, 3 x 256 wide."""
    small = lambda a: np.array(Image.fromarray(a).resize((256, 256), Image.NEAREST))
    base = small(rgb512)
    over = lambda m, c: np.where(small(m.astype(np.uint8) * 255)[..., None] > 0,
                                 (0.55 * base + 0.45 * np.array(c)).astype(np.uint8), base)
    return np.concatenate([base, over(gt512, (0, 255, 0)), over(pred512, (255, 0, 0))], 1), title


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--no-sim', action='store_true')
    args = ap.parse_args()
    out = RESULTS / 'test' / args.tag
    lock = out / 'test_set_used.txt'
    if lock.exists() and not args.force:
        sys.exit(f'{lock} exists: the test set was already evaluated for this model.')
    out.mkdir(parents=True, exist_ok=True)
    lock.write_text(f'test evaluation started {datetime.datetime.now().isoformat()}\n', encoding='utf-8')

    qdir = RESULTS / 'quant' / args.tag
    qs = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    shift = qs['shift']
    k = 1 << shift
    ckpt = torch.load(RESULTS / 'train' / args.tag / 'best.pth', weights_only=True, map_location='cpu')
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    mixed, amix = load_plan(qdir, '')
    int8, a8 = load_plan(qdir, 'int8_')
    qt_mix, qt_8 = qs['output_integer_threshold_ge'], 1   # p > 0.5  <=>  q >= 1 for any positive scale
    x, y, meta = load_split('test')
    cells, rows, cols = load_cells('test')
    runner = None if args.no_sim else SizedRunner(ROOT / 'deploy' / args.tag)
    per, sim_bad = [], []
    torch.set_num_threads(8)
    with torch.inference_mode():
        for i, m in enumerate(meta):
            q = torch.from_numpy(np.ascontiguousarray(x[i, ::k, ::k])).permute(2, 0, 1).unsqueeze(0)
            fp = model(q.float() / 255)[0, 0].numpy() > 0
            r8 = run_integer(int8, a8, q)[0][0, 0].numpy() >= qt_8
            rm = run_integer(mixed, amix, q)[0][0, 0].numpy()
            if runner is not None and not np.array_equal(runner.run(np.ascontiguousarray(x[i, ::k, ::k])), rm):
                sim_bad.append(m['name'])
            preds = dict(fp32=fp, int8=r8, mixed=rm >= qt_mix)
            row = dict(image=m['name'], width=m['width'], height=m['height'], lesion_fraction=m['lesion_fraction'])
            for key, p in preds.items():
                row[key] = isic_metrics(board_upsample(p, shift), cells[i], rows[i], cols[i])
            row['changed_pixels_mixed_vs_fp32'] = int((preds['mixed'] != fp).sum())
            per.append(row)
            if i % 50 == 0:
                print(f"{i + 1}/{len(meta)} {m['name']}: FP32 JA {row['fp32']['jaccard']:.3f} "
                      f"mixed JA {row['mixed']['jaccard']:.3f} sim mismatches {len(sim_bad)}", flush=True)
    if runner is not None:
        runner.close()
    groups = {key: mean_metrics([r[key] for r in per]) for key in ('fp32', 'int8', 'mixed')}
    js = np.array([r['mixed']['jaccard'] for r in per])
    order = np.argsort(js)
    summary = dict(tag=args.tag, resolution=512 >> shift, images=len(per), metrics=groups,
                   drop_fp32_to_mixed=dict((kk, groups['fp32'][kk] - groups['mixed'][kk]) for kk in groups['fp32']),
                   median_jaccard_mixed=float(np.median(js)),
                   jaccard_below_0_5=int((js < 0.5).sum()), jaccard_below_0_65=int((js < 0.65).sum()),
                   sim_bit_exact=None if runner is None else dict(images=len(per), mismatching_images=sim_bad),
                   int16_nodes=qs['selected_int16'], output_integer_threshold_ge=qt_mix,
                   worst=[per[j]['image'] for j in order[:10]], per_image=per)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')

    # visual panels: best / median / worst three by deployed-model Jaccard
    picks = list(order[:3]) + list(order[len(order) // 2 - 1:len(order) // 2 + 2]) + list(order[-3:])
    tiles = []
    for j in picks:
        q = np.ascontiguousarray(x[j, ::k, ::k])
        rm = run_integer(mixed, amix, torch.from_numpy(q).permute(2, 0, 1).unsqueeze(0))[0][0, 0].numpy()
        tiles.append(panel(np.ascontiguousarray(x[j]), y[j] > 0, board_upsample(rm >= qt_mix, shift),
                           f"{meta[j]['name']} JA {js[j]:.3f}")[0])
    Image.fromarray(np.concatenate(tiles, 0)).save(out / 'panels_worst3_median3_best3.png')
    print(json.dumps({kk: v for kk, v in summary.items() if kk not in ('per_image',)}, indent=1))
    lock.write_text(lock.read_text(encoding='utf-8') + f'finished {datetime.datetime.now().isoformat()}\n',
                    encoding='utf-8')


if __name__ == '__main__':
    main()
