"""PTQ of the F3 propagation model (same rules as quantize_echo.py / quantize_inter.py).

  * calibration: 8 simulated (frame, previous mask, previous frame) samples from the
    official training sequences (make_seq_batch, fixed seed) -- what the board receives;
  * INT16 screening on those 8 samples (one node at a time, output node excluded),
    confirmation of all-INT8 / top 3 / top 1+2 with the INTEGER model in the propagation
    loop on the validation sequences (seqeval.run_both, score as in train_seq.py).
Test split untouched. Writes results/quant/<tag>/ (plan, parameters, golden, and the
integer propagation inputs / masks, forward and backward, for the bit-exact checks).

    python quantize_seq.py --tag seq_prev
"""
import argparse
import hashlib
import json

import numpy as np
import torch

from common import CACHE, RESULTS
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test
from models.get_model import get_arch
from seqeval import load_val_seq, run_both
from train_seq import make_seq_batch

N_CAL, BATCH = 8, 25


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
    enc = ckpt['enc']
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    img = torch.from_numpy(np.load(CACHE / 'seq_train_img128.npy'))
    lv = torch.from_numpy(np.load(CACHE / 'seq_train_lv128.npy'))
    f1prior = torch.from_numpy(np.load(CACHE / 'seq_train_f1prior128.npy'))
    index = json.loads((CACHE / 'seq_train_index.json').read_text(encoding='utf-8'))
    start = torch.zeros(len(img), dtype=torch.long)
    length = torch.zeros(len(img), dtype=torch.long)
    for s in index:
        start[s['start']:s['start'] + s['length']] = s['start']
        length[s['start']:s['start'] + s['length']] = s['length']
    gen = torch.Generator().manual_seed(20261008)
    idx = torch.randint(0, len(img), (N_CAL,), generator=gen)
    inp, tgt = make_seq_batch(img, lv, f1prior, start, length, idx, gen, enc)
    cal_q = torch.round(inp * 255).to(torch.uint8)
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
        def predict(rgb):
            outs = []
            with torch.inference_mode():
                for i in range(0, len(rgb), BATCH):
                    q = torch.from_numpy(np.ascontiguousarray(rgb[i:i + BATCH])).permute(0, 3, 1, 2)
                    outs.append(run_integer(plan, arrays, q)[0][:, 0].numpy() >= 1)
            return np.concatenate(outs)
        return predict

    def cal_dice(nodes):
        plan, arrays = make(nodes)
        with torch.inference_mode():
            p = run_integer(plan, arrays, cal_q)[0][:, 0].numpy() >= 1
        return float(np.mean([2 * (a & b).sum() / max(a.sum() + b.sum(), 1) for a, b in zip(p, cal_t)]))

    V = load_val_seq()

    def val_score(nodes):
        r, _ = run_both(int_predict(*make(nodes)), V, enc)
        return dict(nodes=list(nodes), score=r['score'], fwd_es_dice=r['fwd']['es_dice'], bwd_ed_dice=r['bwd']['ed_dice'],
                    fwd_fac_abs_err_pct=r['fwd']['fac_abs_err_pct'])

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
        print(f"confirm {nodes or 'all INT8'}: score {r['score']:.5f} fwd ES {r['fwd_es_dice']:.4f} "
              f"bwd ED {r['bwd_ed_dice']:.4f} FAC err {r['fwd_fac_abs_err_pct']:.2f}", flush=True)
    chosen = sorted(confirm, key=lambda r: (-r['score'], len(r['nodes'])))[0]
    selected = chosen['nodes']

    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}

    def fp_predict(rgb):
        with torch.inference_mode():
            return (model(torch.from_numpy(rgb).permute(0, 3, 1, 2).float() / 255)[:, 0] > 0).numpy()

    r_fp, _ = run_both(fp_predict, V, enc)
    r_q, det = run_both(int_predict(plan, arrays), V, enc, record=True)
    rec = out / 'seq_inputs'
    rec.mkdir(exist_ok=True)
    for d in ('fwd', 'bwd'):
        # step 0 is the F1 result (model 2), so only steps >= 1 are this model's inputs
        np.save(rec / f'{d}_rgb.npy', np.concatenate([r[1:] for r in det[f'rec_{d}']]))
        masks = det[d] if d == 'fwd' else [m[::-1] for m in det[d]]           # back to step order
        np.save(rec / f'{d}_mask.npy', np.concatenate([m[1:] for m in masks]))
        np.save(rec / f'{d}_start_mask.npy', np.stack([m[0] for m in masks]))
    np.save(rec / 'lengths.npy', np.array([s['length'] for s in V['index']]))
    golden_rgb = det['rec_fwd'][0][2]
    gdir = out / 'golden' / 'val_seq000_fwd_step2'
    gdir.mkdir(parents=True, exist_ok=True)
    fp = Observer(graph, capture=True)
    q = torch.from_numpy(golden_rgb).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        fp.run(q.float() / 255)
        run_integer(plan, arrays, q, fp.tensors, gdir)
    np.save(gdir / 'input_rgb.npy', golden_rgb)
    (out / 'per_sequence.json').write_text(json.dumps(dict(fwd=det['rows_fwd'], bwd=det['rows_bwd'])), encoding='utf-8')
    summary = dict(tag=args.tag, encoding=enc, selected_int16=selected, train_iter=ckpt['iter'],
                   calibration='8 simulated training samples (make_seq_batch), seed 20261008',
                   screening=screen, confirmation=confirm, fp32=r_fp, quantized=r_q,
                   output_scale=plan[-1]['scale'], output_integer_threshold_ge=1,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   plan_sha256=sha256(out / 'quant_plan.json'), parameters_sha256=sha256(out / 'parameters.npz'),
                   arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    for name, r in (('FP32', r_fp), ('INT', r_q)):
        print(f"{name}: score {r['score']:.4f} fwd ES {r['fwd']['es_dice']:.4f} FAC err {r['fwd']['fac_abs_err_pct']:.2f} "
              f"jump {r['fwd']['jump_mean']:.4f} | bwd ED {r['bwd']['ed_dice']:.4f} FAC err {r['bwd']['fac_abs_err_pct']:.2f}")
    print('selected INT16', selected, 'output scale', plan[-1]['scale'])


if __name__ == '__main__':
    main()
