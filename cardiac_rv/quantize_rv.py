"""RV / RA programs, step 3: PTQ under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC (same procedure as
echo/quantize_echo.py, lwnet/int8_reference.py reused unchanged):
  * calibration: 8 TRAIN frames (seed 20260923);
  * INT16 screening: every stored arithmetic node except the INT8 output, mean Dice on the 8 calibration frames;
  * confirmation: top 3 single nodes + top-2 combination on VAL (275 frames); at most 2 INT16 nodes;
  * TEST not used.
Board decision: integer logit >= 1 at the 128 tier; Dice at the native crop resolution (common_rv.to_native).
Writes results/quant/<tag>/{quant_plan.json, parameters.npz, int8_*, outputs/val_*.npy, golden/, summary.json}.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I quantize_rv.py --tag rv_r128
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_rv import RESULTS, dice, grouped, load, to_native  # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test  # noqa: E402
from models.get_model import get_arch  # noqa: E402

CAL_SEED, N_CAL = 20260923, 8


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    run = RESULTS / 'train' / args.tag
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    ckpt = torch.load(run / 'best.pth', weights_only=True, map_location='cpu')
    shift, target = int(ckpt['shift']), ckpt['target']
    k = 1 << shift
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)
    xtr, _, gtr, mtr = load('train', target)
    xva, _, gva, mva = load('val', target)
    cal = np.sort(np.random.default_rng(CAL_SEED).choice(len(mtr), N_CAL, replace=False))

    def inp(x512):
        g = torch.from_numpy(np.ascontiguousarray(x512[::k, ::k]))
        q = g.unsqueeze(0).expand(3, -1, -1).unsqueeze(0).contiguous()     # uint8 [1,3,h,w], R=G=B
        return q, q.float() / 255

    observer = Observer(graph)
    with torch.inference_mode():
        for i in cal:
            q, xf = inp(xtr[i])
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def evaluate(nodes, xs, gs, ms, ids):
        plan, arrays = make(nodes)
        per = []
        with torch.inference_mode():
            for i in ids:
                qmap = run_integer(plan, arrays, inp(xs[i])[0])[0][0, 0].numpy()
                per.append(dice(to_native(qmap >= 1, gs[i].shape), gs[i]))
        return dict(nodes=list(nodes), **grouped(per, [ms[i] for i in ids]))

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    cal_args = (xtr, gtr, mtr, cal)
    val_args = (xva, gva, mva, range(len(mva)))
    base = evaluate([], *cal_args)
    screen = []
    for n, node in enumerate(candidates):
        r = evaluate([node], *cal_args)
        r['gain'] = r['all'] - base['all']
        screen.append(r)
        print(f"screen {n + 1}/{len(candidates)} {node}: Dice {r['all']:.5f} gain {r['gain']:+.5f}", flush=True)
    ranking = sorted(screen, key=lambda r: r['all'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = evaluate(nodes, *val_args)
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: val Dice {r['all']:.5f} G {r['site_G']:.5f} R {r['site_R']:.5f}", flush=True)
    chosen = sorted(confirm, key=lambda r: (-r['all'], len(r['nodes'])))[0]
    selected = chosen['nodes']

    p8, a8 = make([])
    (out / 'int8_quant_plan.json').write_text(json.dumps(p8, indent=2), encoding='utf-8')
    np.savez(out / 'int8_parameters.npz', **a8)
    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}

    golden = 0                                       # first VAL frame: dump every node
    (out / 'outputs').mkdir(exist_ok=True)
    fp_per, q_per = [], []
    with torch.inference_mode():
        for i, m in enumerate(mva):
            q, xf = inp(xva[i])
            fp = Observer(graph, capture=i == golden)
            logits = fp.run(xf)
            gdir = out / 'golden' / f"{m['folder']}_{m['video']}_{m['frame']}" if i == golden else None
            if gdir:
                gdir.mkdir(parents=True, exist_ok=True)
            res, _ = run_integer(plan, arrays, q, fp.tensors if i == golden else None, gdir)
            qmap = res[0, 0].numpy()
            np.save(out / 'outputs' / f'val_{i:03d}.npy', qmap.astype(np.int8))
            fp_per.append(dice(to_native(logits[0, 0].numpy() > 0, gva[i].shape), gva[i]))
            q_per.append(dice(to_native(qmap >= 1, gva[i].shape), gva[i]))
    summary = dict(tag=args.tag, target=target, shift=shift, resolution=512 >> shift, selected_int16=selected,
                   calibration=[mtr[i] for i in cal], val_fp32=grouped(fp_per, mva), val_quantized=grouped(q_per, mva),
                   val_all_int8={kk: next(r for r in confirm if not r['nodes'])[kk] for kk in ('all', 'site_G', 'site_R')},
                   screening=screen, confirmation=confirm, output_scale=plan[-1]['scale'],
                   output_integer_threshold_ge=1, golden_val_index=golden,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1, default=str), encoding='utf-8')
    print(f"selected INT16 {selected}; val Dice FP32 {summary['val_fp32']['all']:.4f} | all-INT8 "
          f"{summary['val_all_int8']['all']:.4f} | mixed {summary['val_quantized']['all']:.4f} "
          f"(G {summary['val_quantized']['site_G']:.4f} R {summary['val_quantized']['site_R']:.4f})")


if __name__ == '__main__':
    main()
