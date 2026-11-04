"""③ EchoNet on the real board (v5.8+): single frames, whole videos, and the host UI code path.

A  every VAL ED / ES frame (2,576) as a compact RGB frame (t3, padded 128, model echonet_lv): board int8 logits cropped
   to 112 == results/quant/<tag>/val_outputs.npy; board Dice == offline;
B  whole VAL videos (the first --videos by name), both modes (real time = every k-th frame; full = every frame), streamed
   as fast as the board goes (2 in flight): every board output == the integer model (int8_gpu, itself checked against
   the CPU reference), and the calibrated EF from the board == eval_ef.py's per-video result; per-video wall time,
   frames per second and real-time factor (video duration / board time);
C  (host UI code) is checked by verify_ui_code_echonet.py in the UI environment (OpenCV, no torch).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I verify_board_echonet.py --tag t3 --model 16 --videos 60
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
sys.path.insert(0, str(PLD / 'lwnet'))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

import echo_ws              # noqa: E402
import imagelink            # noqa: E402
import proto                # noqa: E402
from beats import analyze, realtime_step   # noqa: E402
from common import DATA, PAD, RESULTS, load_frames, load_videos, read_video   # noqa: E402
from frame_ops import features, video_inputs   # noqa: E402
from int8_gpu import IntegerModel   # noqa: E402
from train_echonet import dice_batch, to_input   # noqa: E402
from volume import volume_pca   # noqa: E402

F = proto.FLAG_STATS | proto.FLAG_C_LOGIT


def stream(link, x, model):
    """x [m, 3, 128, 128] uint8 -> (board q cropped [m, 112, 112] int8, stats, wall s)."""
    link.close()                      # fresh connection: analysis pauses between streams can exceed
                                      # the server's 10 s idle timeout
    out, sts = [None] * len(x), [None] * len(x)
    inflight, k, t0 = deque(), 0, time.perf_counter()
    while k < len(x) or inflight:
        while len(inflight) < 2 and k < len(x):
            link.send_v6c(np.ascontiguousarray(x[k].transpose(1, 2, 0)).tobytes(), 128, kind=proto.V6C_KIND_RGB,
                          model0=model, thr=1, flags=F)
            inflight.append(k)
            k += 1
        i = inflight.popleft()
        st, lg, _ = link.recv_v6c(F, 128)
        out[i] = np.frombuffer(lg, np.int8).reshape(128, 128)[PAD:PAD + 112, PAD:PAD + 112]
        sts[i] = st
    return np.stack(out), sts, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', default='t3')
    ap.add_argument('--model', type=int, required=True)
    ap.add_argument('--videos', type=int, default=60)
    ap.add_argument('--features', default='t3_int')
    ap.add_argument('--host', default='192.168.1.254')
    args = ap.parse_args()
    qd = RESULTS / 'quant' / args.tag
    summ = json.loads((qd / 'summary.json').read_text(encoding='utf-8'))
    mode_in, scale = summ['input'], json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))[-1]['scale']
    plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
        im = IntegerModel(plan, {k: z[k] for k in z.files})
    conf = json.loads((ROOT.parent / 'tcp_image' / 'tcp_image_ui' / 'echonet_config.json').read_text(encoding='utf-8'))
    ev = json.loads((RESULTS / 'ef' / f'{args.features}_val.json').read_text(encoding='utf-8'))
    evv = {r['video']: r for r in ev['per_video']}
    link = imagelink.ImageLink(args.host, 5060, timeout=30)
    report = dict(utc=datetime.now(timezone.utc).isoformat(), tag=args.tag, model=args.model, A={}, B=[], C={})

    # A
    xva, yva, mva = load_frames('val')
    xv = to_input(np.asarray(xva), mode_in)
    q, sts, wall = stream(link, xv, args.model)
    ref = np.load(qd / 'val_outputs.npy')
    bad = [int(i) for i in np.nonzero((q != ref).any(axis=(1, 2)))[0]]
    yv = np.asarray(yva).astype(bool)
    report['A'] = dict(frames=len(xv), mismatching=bad, wrong_model=sum(st['model'] != args.model for st in sts),
                       dice_board=float(dice_batch(q >= 1, yv).mean()), dice_offline=float(dice_batch(ref >= 1, yv).mean()),
                       fps=len(xv) / wall, ai_ms=float(np.mean([st['ai_us'] for st in sts])) / 1000)
    print('A', report['A'], flush=True)

    # B
    frames, idx = load_videos('val')
    names = sorted(idx)[:args.videos]
    ok_b = True
    for v in names:
        e = idx[v]
        fr = np.asarray(frames[e['start']:e['start'] + e['n']])
        x_all = video_inputs(fr, mode_in, e['fps'])
        row = dict(video=v, frames=e['n'], fps_video=e['fps'], duration_s=e['n'] / e['fps'])
        for mode in ('realtime', 'full'):
            k = realtime_step(e['fps']) if mode == 'realtime' else 1
            x = x_all[::k]
            qb, sts, wall = stream(link, x, args.model)
            with torch.no_grad():
                qi = np.concatenate([im(x[i:i + 128])[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                                     for i in range(0, len(x), 128)])
            vols = [features((qq.astype(np.float32) - 0.5) * scale)[1][3] for qq in qb]
            r = analyze(vols, e['fps'], k, conf['cfg'])
            ef = conf[mode]['a'] * r['ef'] + conf[mode]['b']
            off = evv[v][mode]['ef']
            row[mode] = dict(step=k, analysed=len(x), mismatching=int((qb != qi).any(axis=(1, 2)).sum()),
                             ef_board=ef, ef_offline=off, wall_s=wall, fps=len(x) / wall,
                             realtime_factor=row['duration_s'] / wall, beats=len(r['beats']))
            ok_b &= row[mode]['mismatching'] == 0 and abs(ef - off) < 1e-9
        report['B'].append(row)
        print('B', v, {m: (row[m]['mismatching'], round(row[m]['ef_board'], 2), round(row[m]['ef_offline'], 2),
                           round(row[m]['fps'], 1), round(row[m]['realtime_factor'], 2)) for m in ('realtime', 'full')},
              flush=True)
    for mode in ('realtime', 'full'):
        rows = [r[mode] for r in report['B']]
        report[f'B_{mode}'] = dict(videos=len(rows), analysed=sum(r['analysed'] for r in rows),
                                   wall_s=sum(r['wall_s'] for r in rows),
                                   fps=sum(r['analysed'] for r in rows) / sum(r['wall_s'] for r in rows),
                                   realtime_factor_median=float(np.median([r['realtime_factor'] for r in rows])),
                                   realtime_factor_min=float(np.min([r['realtime_factor'] for r in rows])))
        print(mode, report[f'B_{mode}'], flush=True)

    ok_c = True                       # C (host UI code) runs in the UI environment: verify_ui_code_echonet.py
    report['pass'] = bool(not report['A']['mismatching'] and not report['A']['wrong_model'] and ok_b and ok_c)
    (ROOT / 'board_check').mkdir(exist_ok=True)
    (ROOT / 'board_check' / f'acceptance_echonet_{args.tag}.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('ECHONET_BOARD_PASS' if report['pass'] else 'ECHONET_BOARD_FAIL', flush=True)
    sys.exit(0 if report['pass'] else 1)


if __name__ == '__main__':
    main()
