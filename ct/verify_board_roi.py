"""⑤ stage 2 on the real board (v5.7+): coarse pass (model 14) + ROI fine pass (model 15), both from the board.

A  7 validation cases: board coarse logits == model-14 integer outputs; the window / fine payloads built from the
   board's coarse result; board fine logits == results/quant/<fine>/outputs/<case>.npy; the two-stage 3D Dice from the
   board equals eval_roi.py; per-case time of both passes;
B  the 20 unlabelled imagesTs volumes: two-stage timing (coarse + fine) and a volume estimate only;
C  host UI code (tcp_image_ui/ct_volume.py roi_window / roi_payloads / roi_combine) == roi.py on 7 cases.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I verify_board_roi.py --fine roi_prior --coarse-model 14 --fine-model 15
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402

import ct_volume            # noqa: E402
import imagelink            # noqa: E402
from common import DATA, RESULTS, box, dice3d, load_split, stack_input   # noqa: E402
from roi import coarse_mask, combine, fine_inputs, roi_window   # noqa: E402
from train_ct import load_set   # noqa: E402
from verify_board_ct import stream   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--coarse', default='z3_r128')
    ap.add_argument('--fine', required=True)
    ap.add_argument('--coarse-model', type=int, default=14)
    ap.add_argument('--fine-model', type=int, default=15)
    ap.add_argument('--host', default='192.168.1.254')
    args = ap.parse_args()
    fmode = json.loads((RESULTS / 'quant' / args.fine / 'summary.json').read_text(encoding='utf-8'))['input']
    ev = json.loads((RESULTS / 'eval' / f'two_stage_{args.fine}' / 'val.json').read_text(encoding='utf-8'))
    link = imagelink.ImageLink(args.host, 5060, timeout=30)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), coarse=args.coarse, fine=args.fine,
                  models=[args.coarse_model, args.fine_model], A=[], B=[], C={})

    va = load_set(load_split()['val'])
    ok = True
    for i, c in enumerate(va['cases']):
        a, b = va['spans'][i]
        sp, img, gt = va['spacing'][i], va['img'][a:b], va['lab'][a:b].astype(bool)
        x = box(stack_input(img, 'z3', sp[2]).transpose(0, 3, 1, 2), 4).transpose(0, 2, 3, 1)
        q0, s0, w0 = stream(link, [np.ascontiguousarray(s).tobytes() for s in x], args.coarse_model, 1)
        m0 = coarse_mask(q0)
        win = roi_window(m0, sp[2])
        xf = fine_inputs(img, m0, win, fmode, sp[2])
        q1, s1, w1 = stream(link, [np.ascontiguousarray(s).tobytes() for s in xf], args.fine_model, 1)
        ref0 = np.load(RESULTS / 'quant' / args.coarse / 'outputs' / f'{c}.npy')
        ref1 = np.load(RESULTS / 'quant' / args.fine / 'outputs' / f'{c}.npy')
        d = dice3d(combine(q1, win, b - a, 0.5), gt)
        off = next(r for r in ev['per_case'] if r['case'] == c)['two']['dice']
        row = dict(case=c, slices=b - a, fine_slices=len(xf), window=list(win),
                   coarse_mismatch=int(sum(not np.array_equal(q0[z], ref0[z]) for z in range(len(q0)))),
                   fine_mismatch=int(sum(not np.array_equal(q1[z], ref1[z]) for z in range(len(q1)))) if q1.shape == ref1.shape else -1,
                   wrong_model=[z for z, st in enumerate(s0) if st['model'] != args.coarse_model]
                   + [z for z, st in enumerate(s1) if st['model'] != args.fine_model],
                   dice_board=d, dice_offline=off, coarse_s=w0, fine_s=w1, total_s=w0 + w1)
        ok &= row['coarse_mismatch'] == 0 and row['fine_mismatch'] == 0 and not row['wrong_model'] and abs(d - off) < 1e-12
        report['A'].append(row)
        print('A', row, flush=True)

    for f in sorted((DATA / 'imagesTs').glob('*.nii.gz'), key=lambda p: int(p.name.split('_')[1].split('.')[0])):
        vol = ct_volume.load_ct(str(f))
        u8 = ct_volume.window_u8(vol['hu'])
        dz = vol['spacing'][2]
        q0, _, w0 = stream(link, ct_volume.payloads(u8, dz, 'z3'), args.coarse_model, 1)
        m0 = ct_volume.postprocess(q0, 'bilinear+lcc')
        win = ct_volume.roi_window(m0, dz)
        if win is None:
            fin, nf, w1 = m0, 0, 0.0
        else:
            pl = ct_volume.roi_payloads(u8, m0, win, fmode, dz)
            q1, _, w1 = stream(link, pl, args.fine_model, 1)
            fin, nf = ct_volume.roi_combine(q1, win, len(u8)), len(pl)
        row = dict(case=f.name.split('.')[0], slices=len(u8), fine_slices=nf, coarse_s=w0, fine_s=w1, total_s=w0 + w1,
                   spleen_ml_single=round(ct_volume.volume_ml(m0, vol['spacing']), 1),
                   spleen_ml_two=round(ct_volume.volume_ml(fin, vol['spacing']), 1))
        report['B'].append(row)
        print('B', row, flush=True)
    rows = report['A'] + report['B']
    report['totals'] = dict(volumes=len(rows), slices=sum(r['slices'] for r in rows),
                            fine_slices=sum(r['fine_slices'] for r in rows),
                            coarse_s=sum(r['coarse_s'] for r in rows), fine_s=sum(r['fine_s'] for r in rows))
    t = report['totals']
    t['fine_fraction'] = t['fine_slices'] / t['slices']
    t['time_overhead_pct'] = 100 * t['fine_s'] / t['coarse_s']
    t['mean_total_s_per_volume'] = (t['coarse_s'] + t['fine_s']) / t['volumes']

    ok_c = True
    for i, c in enumerate(va['cases']):
        a, b = va['spans'][i]
        sp, img = va['spacing'][i], va['img'][a:b]
        q0 = np.load(RESULTS / 'quant' / args.coarse / 'outputs' / f'{c}.npy')
        q1 = np.load(RESULTS / 'quant' / args.fine / 'outputs' / f'{c}.npy')
        m0 = coarse_mask(q0)
        win = roi_window(m0, sp[2])
        m0u = ct_volume.postprocess(q0, 'bilinear+lcc')
        winu = ct_volume.roi_window(m0u, sp[2])
        plu = ct_volume.roi_payloads(img, m0u, winu, fmode, sp[2])
        xf = fine_inputs(img, m0, win, fmode, sp[2])
        r = dict(coarse_mask=bool(np.array_equal(m0, m0u)), window=tuple(win) == tuple(winu),
                 payloads=len(plu) == len(xf) and all(p == np.ascontiguousarray(s).tobytes() for p, s in zip(plu, xf)),
                 final=bool(np.array_equal(combine(q1, win, b - a, 0.5), ct_volume.roi_combine(q1, winu, b - a))))
        report['C'][c] = r
        ok_c &= all(r.values())
        print('C', c, r, flush=True)
    report['pass'] = bool(ok and ok_c)
    (ROOT / 'board_check').mkdir(exist_ok=True)
    (ROOT / 'board_check' / f'acceptance_roi_{args.fine}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report['totals']))
    print('ROI_BOARD_PASS' if report['pass'] else 'ROI_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
