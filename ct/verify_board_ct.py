"""⑤ CT spleen on the real board (v5.6+): whole volumes streamed slice by slice, bit-exact against the integer model.

A  every slice of the 7 validation cases, sent as compact RGB frames (model ct_spleen, 128 tier, 2 in flight):
   board int8 logits == results/quant/<tag>/outputs/<case>.npy (the integer reference), stats report the model;
   the 3D Dice from the board's outputs (chosen post-processing) equals the offline evaluation;
B  throughput: per-volume wall time, slices per second, PL time per slice; plus the 20 unlabelled imagesTs volumes
   (never used anywhere: no labels) for timing and a volume estimate only;
C  the host UI code path (tcp_image_ui/ct_volume.py: reading, orientation, window, payloads, post-processing)
   gives the same payload bytes and the same masks as this offline code.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I verify_board_ct.py --tag z3_r128 --model 14
"""
import argparse
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402

import ct_volume            # noqa: E402
import imagelink            # noqa: E402
import proto                # noqa: E402
from common import DATA, RESULTS, box, dice3d, load_split, stack_input   # noqa: E402
from eval_ct import upsample  # noqa: E402
from common import largest_cc3d  # noqa: E402
from train_ct import load_set  # noqa: E402


def stream(link, payloads, model, thr):
    """send every payload (2 in flight) -> (int8 logits [z,128,128], stats list, wall seconds)."""
    flags = proto.FLAG_STATS | proto.FLAG_C_LOGIT
    out, stats = [None] * len(payloads), [None] * len(payloads)
    inflight = deque()
    t0 = time.perf_counter()
    k = 0
    while k < len(payloads) or inflight:
        while len(inflight) < 2 and k < len(payloads):
            link.send_v6c(payloads[k], 128, kind=proto.V6C_KIND_RGB, model0=model, thr=thr, flags=flags)
            inflight.append(k)
            k += 1
        z = inflight.popleft()
        st, lg, _ = link.recv_v6c(flags, 128)
        out[z] = np.frombuffer(lg, np.int8).reshape(128, 128)
        stats[z] = st
    return np.stack(out), stats, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--model', type=int, required=True)
    ap.add_argument('--host', default='192.168.1.254')
    args = ap.parse_args()
    qdir = RESULTS / 'quant' / args.tag
    summary = json.loads((qdir / 'summary.json').read_text(encoding='utf-8'))
    mode, k = summary['input'], 512 // summary['res']
    sel = json.loads((RESULTS / 'eval' / args.tag / 'val.json').read_text(encoding='utf-8'))
    chosen = sel['chosen']
    link = imagelink.ImageLink(args.host, 5060, timeout=30)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), tag=args.tag, model=args.model, post=chosen, A=[], B=[], C={})

    va = load_set(load_split()['val'])
    ok_a = True
    for i, c in enumerate(va['cases']):
        a, b = va['spans'][i]
        x = box(stack_input(va['img'][a:b], mode, va['spacing'][i][2]).transpose(0, 3, 1, 2), k).transpose(0, 2, 3, 1)
        pl = [np.ascontiguousarray(s).tobytes() for s in x]
        q, sts, wall = stream(link, pl, args.model, 1)
        ref = np.load(qdir / 'outputs' / f'{c}.npy')
        bad = [z for z in range(len(q)) if not np.array_equal(q[z], ref[z])]
        wrong_model = [z for z, st in enumerate(sts) if st is None or st.get('model') != args.model]
        m = upsample(q, chosen.split('+')[0], k)
        if chosen.endswith('+lcc'):
            m = largest_cc3d(m)
        d = dice3d(m, va['lab'][a:b].astype(bool))
        off = next(r for r in sel['per_case'] if r['case'] == c)[chosen]['dice']
        row = dict(case=c, slices=len(q), mismatching_slices=bad, wrong_model=wrong_model, dice_board=d,
                   dice_offline=off, wall_s=wall, slices_per_s=len(q) / wall,
                   ai_ms_mean=float(np.mean([st['ai_us'] for st in sts])) / 1000)
        ok_a &= not bad and not wrong_model and abs(d - off) < 1e-12
        report['A'].append(row)
        print('A', row, flush=True)

    for f in sorted((DATA / 'imagesTs').glob('*.nii.gz'), key=lambda p: int(p.name.split('_')[1].split('.')[0])):
        vol = ct_volume.load_ct(str(f))
        u8 = ct_volume.window_u8(vol['hu'])
        pl = ct_volume.payloads(u8, vol['spacing'][2], mode)
        q, sts, wall = stream(link, pl, args.model, 1)
        fin = ct_volume.postprocess(q, chosen)
        row = dict(case=f.name.split('.')[0], slices=len(q), wall_s=wall, slices_per_s=len(q) / wall,
                   ai_ms_mean=float(np.mean([st['ai_us'] for st in sts])) / 1000,
                   spleen_ml_estimate=round(ct_volume.volume_ml(fin, vol['spacing']), 1))
        report['B'].append(row)
        print('B', row, flush=True)
    sl = sum(r['slices'] for r in report['B'] + report['A'])
    wall = sum(r['wall_s'] for r in report['B'] + report['A'])
    report['B_total'] = dict(volumes=len(report['B']) + len(report['A']), slices=sl, wall_s=wall, slices_per_s=sl / wall)

    # C: the UI's own reading + payloads + post-processing on two validation cases
    ok_c = True
    for i in (0, len(va['cases']) - 1):
        c = va['cases'][i]
        a, b = va['spans'][i]
        vol = ct_volume.load_ct(str(DATA / 'imagesTr' / f'{c}.nii.gz'))
        u8 = ct_volume.window_u8(vol['hu'])
        pl_ui = ct_volume.payloads(u8, vol['spacing'][2], mode)
        x = box(stack_input(va['img'][a:b], mode, va['spacing'][i][2]).transpose(0, 3, 1, 2), k).transpose(0, 2, 3, 1)
        same_pl = all(p == np.ascontiguousarray(s).tobytes() for p, s in zip(pl_ui, x)) and len(pl_ui) == len(x)
        ref = np.load(qdir / 'outputs' / f'{c}.npy')
        m_off = upsample(ref, chosen.split('+')[0], k)
        if chosen.endswith('+lcc'):
            m_off = largest_cc3d(m_off)
        same_mask = bool(np.array_equal(ct_volume.postprocess(ref, chosen), m_off))
        same_lab = bool(np.array_equal(vol['label'], va['lab'][a:b].astype(bool)))
        report['C'][c] = dict(payloads_identical=same_pl, masks_identical=same_mask, labels_identical=same_lab)
        ok_c &= same_pl and same_mask and same_lab
        print('C', c, report['C'][c], flush=True)

    report['pass'] = bool(ok_a and ok_c)
    (ROOT / 'board_check').mkdir(exist_ok=True)
    (ROOT / 'board_check' / f'acceptance_ct_{args.tag}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report['B_total']))
    print('CT_BOARD_PASS' if report['pass'] else 'CT_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
