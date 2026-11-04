"""PTQ of the F2 click-correction model (same rules as quantize_echo.py).

Differences from F1, all forced by the 3-channel encoding:
  * calibration inputs are 8 simulated (frame, prior, clicks) samples from the
    official training sequences (make_batch with a fixed seed), not plain frames;
  * INT16 confirmation runs the standard click protocol on the official
    validation ED / ES frames with the INTEGER model in the loop (k = 0..3, from
    the F1 board result and from empty), score as in train_inter.py.
Test split untouched. Writes results/quant/<tag>/ (plan, parameters, golden,
the integer click-curve inputs/outputs used later for bit-exact checks).

    python quantize_inter.py --tag inter_r128
"""
import argparse
import hashlib
import json

import numpy as np
import torch

from common import CACHE, RESULTS
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test
from models.get_model import get_arch
from train_inter import click_curve, load_val, make_batch

N_CAL, MAX_INT16, K_CONFIRM = 8, 2, 3


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    ckpt = torch.load(RESULTS / 'train' / args.tag / 'best.pth', weights_only=True, map_location='cpu')
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    # calibration: simulated training inputs (CPU generator, fixed seed)
    img = torch.from_numpy(np.load(CACHE / 'seq_train_img128.npy'))
    lv = torch.from_numpy(np.load(CACHE / 'seq_train_lv128.npy'))
    f1prior = torch.from_numpy(np.load(CACHE / 'seq_train_f1prior128.npy'))
    mix = json.loads((RESULTS / 'train' / args.tag / 'summary.json').read_text(encoding='utf-8'))['args'].get('mix', 'v1')
    index = json.loads((CACHE / 'seq_train_index.json').read_text(encoding='utf-8'))
    start = torch.zeros(len(img), dtype=torch.long)
    length = torch.zeros(len(img), dtype=torch.long)
    for s in index:
        start[s['start']:s['start'] + s['length']] = s['start']
        length[s['start']:s['start'] + s['length']] = s['length']

    def nbr_of(idx, g):
        t = idx - start[idx]
        k = torch.randint(1, 7, idx.shape, generator=g) * (torch.randint(0, 2, idx.shape, generator=g) * 2 - 1)
        return start[idx] + torch.clamp(t + k, torch.zeros_like(t), length[idx] - 1)

    gen = torch.Generator().manual_seed(20260923)
    idx = torch.randint(0, len(img), (N_CAL,), generator=gen)
    inp, tgt = make_batch(img, lv, nbr_of, idx, gen, f1prior, mix)     # same simulation as training
    cal_q = torch.round(inp * 255).to(torch.uint8)                     # what the board would receive
    cal_t = tgt[:, 0].numpy() > 0.5

    observer = Observer(graph)
    with torch.inference_mode():
        for i in range(N_CAL):
            xf = cal_q[i:i + 1].float() / 255
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def int_predict(plan, arrays):
        def predict(rgbs):
            outs = []
            with torch.inference_mode():
                for rgb in rgbs:
                    q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
                    outs.append(run_integer(plan, arrays, q)[0][0, 0].numpy() >= 1)
            return outs
        return predict

    def cal_dice(nodes):
        plan, arrays = make(nodes)
        ds = []
        with torch.inference_mode():
            for i in range(N_CAL):
                p = run_integer(plan, arrays, cal_q[i:i + 1])[0][0, 0].numpy() >= 1
                t = cal_t[i]
                ds.append(2 * (p & t).sum() / max(p.sum() + t.sum(), 1))
        return float(np.mean(ds))

    img128, gt128, f1, cells, rows, cols, meta = load_val()
    empty = [np.zeros_like(p) for p in f1]

    def val_score(nodes):
        predict = int_predict(*make(nodes))
        d_f1, _ = click_curve(predict, img128, gt128, f1, cells, rows, cols, K_CONFIRM)
        d_em, _ = click_curve(predict, img128, gt128, empty, cells, rows, cols, K_CONFIRM)
        c_f1, c_em = [float(np.mean(d)) for d in d_f1], [float(np.mean(d)) for d in d_em]
        return dict(nodes=list(nodes), from_f1=c_f1, from_empty=c_em,
                    score=0.5 * float(np.mean(c_f1)) + 0.5 * float(np.mean(c_em[1:])))

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    base = cal_dice([])
    screen = []
    for n, node in enumerate(candidates):
        d = cal_dice([node])
        screen.append(dict(nodes=[node], dice=d, gain=d - base))
        print(f'screen {n + 1}/{len(candidates)} {node}: Dice {d:.5f} gain {d - base:+.5f}', flush=True)
    ranking = sorted(screen, key=lambda r: r['dice'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = val_score(nodes)
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: score {r['score']:.5f} F1 k0..3 "
              + ' '.join(f'{v:.4f}' for v in r['from_f1']), flush=True)
    chosen = sorted(confirm, key=lambda r: (-r['score'], len(r['nodes'])))[0]
    selected = chosen['nodes']

    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}

    # FP32 vs integer click curves (k = 0..5), and the integer inputs / outputs for bit-exact checks
    def fp_predict(rgbs):
        with torch.inference_mode():
            xb = torch.from_numpy(np.stack(rgbs)).permute(0, 3, 1, 2).float() / 255
            return list((model(xb)[:, 0] > 0).numpy())

    record = {}

    def recording(predict, tag):
        def wrapped(rgbs):
            outs = predict(rgbs)
            record.setdefault(tag, []).append((np.stack(rgbs), np.stack(outs)))
            return outs
        return wrapped

    curves = {}
    for name, prior in (('from_f1', f1), ('from_empty', empty)):
        dfp, _ = click_curve(fp_predict, img128, gt128, prior, cells, rows, cols)
        dq, clicks = click_curve(recording(int_predict(plan, arrays), name), img128, gt128, prior, cells, rows, cols)
        curves[name] = dict(fp32=[float(np.mean(d)) for d in dfp], quantized=[float(np.mean(d)) for d in dq],
                            noc90_quantized=float(np.mean([next((k for k in range(len(dq)) if dq[k][i] >= 0.9), 6)
                                                           for i in range(len(f1))])))
    rec = out / 'click_inputs'
    rec.mkdir(exist_ok=True)
    for name, steps in record.items():
        np.save(rec / f'{name}_rgb.npy', np.stack([s[0] for s in steps]))      # (k, frames, 128, 128, 3)
        np.save(rec / f'{name}_mask.npy', np.stack([s[1] for s in steps]))     # integer model masks
    golden_rgb = record['from_f1'][2][0][0]                                    # frame 0 after 2 clicks
    gdir = out / 'golden' / 'val000_from_f1_k2'
    gdir.mkdir(parents=True, exist_ok=True)
    fp = Observer(graph, capture=True)
    q = torch.from_numpy(golden_rgb).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        fp.run(q.float() / 255)
        res, _ = run_integer(plan, arrays, q, fp.tensors, gdir)
    np.save(gdir / 'input_rgb.npy', golden_rgb)
    summary = dict(tag=args.tag, selected_int16=selected, training_mix=mix,
                   calibration='8 simulated training samples (training mix), seed 20260923',
                   screening=screen, confirmation=confirm, curves=curves, output_scale=plan[-1]['scale'],
                   output_integer_threshold_ge=1,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   plan_sha256=sha256(out / 'quant_plan.json'), parameters_sha256=sha256(out / 'parameters.npz'),
                   arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    for name, c in curves.items():
        print(f"{name}: FP32 " + ' '.join(f'{v:.4f}' for v in c['fp32']) + " | INT "
              + ' '.join(f'{v:.4f}' for v in c['quantized']) + f" | NoC90 {c['noc90_quantized']:.2f}")
    print('selected INT16', selected)


if __name__ == '__main__':
    main()
