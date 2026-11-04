"""Real-board acceptance of the ISIC skin server (run after `deploy_skin.py switch skin`).

Only talks TCP to the running server on 192.168.1.254:5060 (v5 board: the skin
model is chosen per frame via aiparam; older boards: the skin service); never deploys,
switches services or loads a bitstream.

  1. golden: ISIC_0000010 (validation image) — all 128x128 logits returned by
     the board == results/quant/r128/outputs (integer reference);
  2. threshold: thr -3 vs 1 changes the mask, not the logits;
  3. the 6 demo samples (samples/*.ppm, exactly what the UI sends) — logits ==
     integer reference recomputed on the same bytes;
  4. all 379 official test images — logits == integer reference, and the
     ISIC metrics recomputed from the BOARD's logits (must reproduce
     results/test/r128/summary.json exactly); per-frame AI time.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe verify_board_skin.py [--quick]
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone

import numpy as np
import torch
from PIL import Image

from common import PLD, RESULTS, ROOT, board_upsample, isic_metrics, load_cells, load_split, mean_metrics
from int8_reference import run_integer

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import confidence  # noqa: E402
import imagelink   # noqa: E402
import proto       # noqa: E402

TAG = 'r128'
RES, SHIFT, THR = 128, 2, 1
MODEL = 1           # skin in the v5 model library (tcp_image_ui/proto.py MODELS)
FLAGS = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--quick', action='store_true', help='skip the 379-image test-set pass')
    ap.add_argument('--host', default='192.168.1.254')
    ap.add_argument('--port', type=int, default=5060)
    ap.add_argument('--test-limit', type=int, default=None, help='only the first N test images (dry runs)')
    args = ap.parse_args()
    out = ROOT / 'board_check'
    out.mkdir(exist_ok=True)
    qdir = RESULTS / 'quant' / TAG
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    assert json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))['output_integer_threshold_ge'] == THR

    def reference(rgb512):
        q = torch.from_numpy(np.array(rgb512[::4, ::4])).permute(2, 0, 1).unsqueeze(0)
        with torch.inference_mode():
            return run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int64)

    link = imagelink.ImageLink(args.host, args.port, timeout=20)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), host=args.host, tag=TAG, checks=[], pass_=False)

    def exchange(rgb512, thr=THR):
        imgs, times, rows, stats = link.round_trip(rgb512.tobytes(), mode=0, flags=FLAGS,
                                                   aiparam=proto.make_aiparam(0, thr, RES, MODEL))
        assert stats is not None and stats['ai_us'] > 0, 'AI did not run'
        assert stats['ai_res'] == RES, f"board ran at {stats['ai_res']}, expected {RES}"
        assert stats['version'] < 5 or stats.get('model') == MODEL, f"board used model {stats.get('model')}"
        assert all(s['crc_errors'] == 0 and s['seq_errors'] == 0 and s['rows_ok'] == 512 for s in rows.values())
        plane = np.frombuffer(confidence.logit_plane(imgs['logit']), np.uint8).reshape(512, 512)
        logits = plane[::4, ::4].astype(np.int64) - proto.LOGIT_IMG_OFFSET
        # the returned 512 logit image must be exactly the 4x4 repeat of the 128 grid
        assert np.array_equal(plane.astype(np.int64) - proto.LOGIT_IMG_OFFSET, board_upsample(logits, SHIFT))
        mask = np.frombuffer(imgs['mask'], np.uint8).reshape(512, 512, 3)[:, :, 0] > 0
        assert np.array_equal(mask, board_upsample(logits >= thr, SHIFT)), 'board mask != board logits >= thr'
        return logits, mask, stats, times

    def check(name, ok, **info):
        report['checks'].append(dict(name=name, pass_=bool(ok), **info))
        print(('PASS ' if ok else 'FAIL ') + name, json.dumps(info, ensure_ascii=False)[:300], flush=True)
        return ok

    ok = True
    xtr, _, mtr = load_split('train')
    gi = next(i for i, m in enumerate(mtr) if m['name'] == 'ISIC_0000010')
    gl, _, st, tm = exchange(np.array(xtr[gi]))
    ref = np.load(qdir / 'outputs' / 'ISIC_0000010.npy').astype(np.int64)
    ok &= check('golden_ISIC_0000010_logits', np.array_equal(gl, ref), mismatches=int((gl != ref).sum()),
                pixels=ref.size, ai_us=st['ai_us'], total_ms=round(tm['total_ms'], 1))

    l3, m3, _, _ = exchange(np.array(xtr[gi]), thr=-3)
    ok &= check('threshold_changes_mask_not_logits', np.array_equal(l3, gl) and m3.sum() >= board_upsample(gl >= THR, SHIFT).sum(),
                lesion_px_thr1=int(board_upsample(gl >= THR, SHIFT).sum()), lesion_px_thr_minus3=int(m3.sum()))

    xte, _, mte = load_split('test')
    cells, rows_, cols_ = load_cells('test')
    by_name = {m['name']: i for i, m in enumerate(mte)}
    for p in sorted((ROOT / 'samples').glob('ISIC_*[0-9].ppm')):
        rgb = np.array(Image.open(p).convert('RGB'))
        assert rgb.shape == (512, 512, 3) and np.array_equal(rgb, xte[by_name[p.stem]])
        lg, _, st, tm = exchange(rgb)
        i = by_name[p.stem]
        ok &= check(f'sample_{p.stem}', np.array_equal(lg, reference(rgb)), ai_us=st['ai_us'],
                    total_ms=round(tm['total_ms'], 1),
                    board_jaccard=round(isic_metrics(board_upsample(lg >= THR, SHIFT), cells[i], rows_[i], cols_[i])['jaccard'], 4))

    if not args.quick:
        offline = {r['image']: r['mixed'] for r in
                   json.loads((RESULTS / 'test' / TAG / 'summary.json').read_text(encoding='utf-8'))['per_image']}
        per, bad, ai_us, total = [], [], [], []
        t0 = time.time()
        for i, m in enumerate(mte[:args.test_limit]):
            lg, _, st, tm = exchange(np.array(xte[i]))
            if not np.array_equal(lg, reference(np.array(xte[i]))):
                bad.append(m['name'])
            met = isic_metrics(board_upsample(lg >= THR, SHIFT), cells[i], rows_[i], cols_[i])
            if abs(met['jaccard'] - offline[m['name']]['jaccard']) > 1e-12:
                bad.append(m['name'] + ':metric')
            per.append(met)
            ai_us.append(st['ai_us'])
            total.append(tm['total_ms'])
            if i % 50 == 0:
                print(f'test {i + 1}/{len(mte)} mismatches {len(bad)} ({time.time() - t0:.0f}s)', flush=True)
        mm = mean_metrics(per)
        ok &= check(f'test_set_{len(per)}_on_board', not bad, mismatching=bad[:20], board_metrics=mm,
                    ai_ms=dict(mean=float(np.mean(ai_us)) / 1000, min=min(ai_us) / 1000, max=max(ai_us) / 1000),
                    round_trip_ms=dict(mean=float(np.mean(total)), min=min(total), max=max(total)),
                    seconds=round(time.time() - t0, 1))

    report['pass_'] = bool(ok)
    name = 'acceptance_quick.json' if args.quick else 'acceptance.json'
    if args.host == '127.0.0.1':
        name = 'dryrun_' + name
    (out / name).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    print('SKIN_BOARD_ACCEPTANCE_PASS' if ok else 'SKIN_BOARD_ACCEPTANCE_FAIL', flush=True)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
