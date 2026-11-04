"""PTQ of the CAMUS LV model under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules.

Same procedure as skin/quantize_skin.py (lwnet/int8_reference.py reused unchanged):
  * calibration: 8 frames drawn (seed 20260923) from the official training set;
  * INT16 screening: every stored arithmetic node except the INT8 output,
    mean Dice on the 8 calibration frames;
  * confirmation: top 3 single nodes + top-2 combination on the official
    validation set (200 frames); at most 2 INT16 nodes;
  * the official test set is not used here.
Input = grey replicated to the 3 channels, exactly what the board receives.

    python quantize_echo.py --tag lv_r128
"""
import argparse
import hashlib
import json

import numpy as np
import torch

from common import RESULTS, board_upsample, grouped, load_cache, seg_metrics
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test
from models.get_model import get_arch

CAL_SEED, N_CAL, MAX_INT16 = 20260923, 8, 2


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    ap.add_argument('--target', default='lv', choices=('lv', 'la', 'epi'), help='lv = F1; la / epi = P1 1.6')
    args = ap.parse_args()
    run = RESULTS / 'train' / args.tag
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    ckpt = torch.load(run / 'best.pth', weights_only=True, map_location='cpu')
    shift = int(ckpt['shift'])
    k = 1 << shift
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    xtr, _, ctr, rtr, qtr, mtr = load_cache('train', args.target)
    xva, _, cva, rva, qva, mva = load_cache('val', args.target)
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

    def evaluate(nodes, xs, cs, rs, qs, ms, ids):
        plan, arrays = make(nodes)
        per = []
        with torch.inference_mode():
            for i in ids:
                qmap = run_integer(plan, arrays, inp(xs[i])[0])[0][0, 0].numpy()
                per.append(seg_metrics(board_upsample(qmap >= 1, shift), cs[i], rs[i], qs[i]))
        return dict(nodes=list(nodes), **grouped(per, [ms[i] for i in ids]))

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    cal_args = (xtr, ctr, rtr, qtr, mtr, cal)
    val_args = (xva, cva, rva, qva, mva, range(len(mva)))
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
        print(f"confirm {nodes or 'all INT8'}: val Dice {r['all']:.5f} good+med {r['good+medium']:.5f}", flush=True)
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

    golden = 0                                       # first validation frame: dump every node
    (out / 'outputs').mkdir(exist_ok=True)
    fp_per, q_per = [], []
    with torch.inference_mode():
        for i, m in enumerate(mva):
            q, xf = inp(xva[i])
            fp = Observer(graph, capture=i == golden)
            logits = fp.run(xf)
            gdir = out / 'golden' / f"{m['patient']}_{m['view']}_{m['phase']}" if i == golden else None
            if gdir:
                gdir.mkdir(parents=True, exist_ok=True)
            res, stats = run_integer(plan, arrays, q, fp.tensors if i == golden else None, gdir)
            qmap = res[0, 0].numpy()
            np.save(out / 'outputs' / f"val_{i:03d}.npy", qmap.astype(np.int8))
            fp_per.append(seg_metrics(board_upsample(logits[0, 0].numpy() > 0, shift), cva[i], rva[i], qva[i]))
            q_per.append(seg_metrics(board_upsample(qmap >= 1, shift), cva[i], rva[i], qva[i]))
    summary = dict(tag=args.tag, shift=shift, resolution=512 >> shift, selected_int16=selected,
                   calibration=[mtr[i] for i in cal], val_fp32=grouped(fp_per, mva), val_quantized=grouped(q_per, mva),
                   val_all_int8={kk: next(r for r in confirm if not r['nodes'])[kk] for kk in ('all', 'good+medium')},
                   screening=screen, confirmation=confirm, output_scale=plan[-1]['scale'],
                   output_integer_threshold_ge=1, golden_val_index=golden,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(f"selected INT16 {selected}; val Dice FP32 {summary['val_fp32']['all']:.4f} | all-INT8 "
          f"{summary['val_all_int8']['all']:.4f} | mixed {summary['val_quantized']['all']:.4f} "
          f"(good+med {summary['val_quantized']['good+medium']:.4f})")


if __name__ == '__main__':
    main()
