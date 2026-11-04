"""PTQ of the ISIC model under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules.

Same arithmetic as the fundus model (lwnet/int8_reference.py, reused unchanged):
per-output-channel symmetric INT8 weights, per-tensor max-abs activations from
8 calibration images, fused Conv->ReLU->BN, ties-to-even, INT8 -127..127 or
INT16 -32767..32767 activations.

Data use:
  * calibration: 8 images drawn (seed 20260923) from the 720 training part only;
  * mixed-precision screening: the same 8 calibration images;
  * confirmation of the top candidates: the 180-image validation part
    (also from the official training set);
  * the official test set is NOT used here (see final_test.py).

Unlike the fundus spec, screening uses all 8 calibration images (not 2): at
128x128 it is cheap, and lesion Jaccard on 2 images is too noisy.

    python quantize_skin.py --tag r128
"""
import argparse
import hashlib
import json
import math

import numpy as np
import torch

from common import RESULTS, board_upsample, isic_metrics, load_cells, load_split, mean_metrics, train_val_indices
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test
from models.get_model import get_arch

CAL_SEED = 20260923
N_CAL = 8
MAX_INT16 = 2          # same budget as the fundus model


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True, help='training run under results/train/')
    ap.add_argument('--golden', default=None, help='val image whose every node is dumped (default: first val image)')
    args = ap.parse_args()
    run = RESULTS / 'train' / args.tag
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    print(tests, flush=True)

    ckpt = torch.load(run / 'best.pth', weights_only=True, map_location='cpu')
    shift = int(ckpt['shift'])
    k = 1 << shift
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    x, _, meta = load_split('train')
    cells, rows, cols = load_cells('train')
    tr, va = train_val_indices()
    cal = np.sort(np.random.default_rng(CAL_SEED).choice(tr, N_CAL, replace=False))
    names = {int(i): meta[i]['name'] for i in np.concatenate([cal, va])}
    golden_id = int(va[0]) if args.golden is None else next(i for i in va if meta[i]['name'] == args.golden)

    def inp(i):
        q = torch.from_numpy(np.ascontiguousarray(x[i, ::k, ::k])).permute(2, 0, 1).unsqueeze(0)
        return q, q.float() / 255

    observer = Observer(graph)
    with torch.inference_mode():
        for i in cal:
            q, xf = inp(i)
            assert torch.equal(observer.run(xf), model(xf)), 'FX graph changed FP32 output'
    ranges = dict(observer.ranges)

    def make(int16_nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in int16_nodes})
        return fuse_conv_affine(graph, p, a)

    def q_threshold(plan):
        # FP32 rule is logit > 0 (p > 0.5); dequantised q*scale > 0  <=>  q >= 1
        return math.floor(0.0 / plan[-1]['scale']) + 1

    fp_cache = {}

    def fp32_logits(i):
        if i not in fp_cache:
            with torch.inference_mode():
                fp_cache[i] = model(inp(i)[1])[0, 0].numpy()
        return fp_cache[i]

    def evaluate(int16_nodes, ids, keep=False):
        plan, arrays = make(int16_nodes)
        qt = q_threshold(plan)
        per, outputs = [], {}
        with torch.inference_mode():
            for i in ids:
                result, _ = run_integer(plan, arrays, inp(i)[0])
                qmap = result[0, 0].numpy()
                assert np.array_equal(qmap >= qt, qmap.astype(np.float64) * plan[-1]['scale'] > 0)
                per.append(dict(image=names[int(i)], **isic_metrics(board_upsample(qmap >= qt, shift),
                                                                   cells[i], rows[i], cols[i])))
                if keep:
                    outputs[int(i)] = qmap
        return dict(nodes=list(int16_nodes), **mean_metrics(per), per_image=per), outputs

    def fp32_eval(ids):
        per = [dict(image=names[int(i)], **isic_metrics(board_upsample(fp32_logits(i) > 0, shift),
                                                        cells[i], rows[i], cols[i])) for i in ids]
        return dict(**mean_metrics(per), per_image=per)

    base_plan, _ = make([])
    # the output stays INT8: the board's unet_run() hands int8_t logits to the
    # mask / overlay / heat-map code (MIXED_PRECISION_SPEC: "输出仍 INT8")
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    protocol = dict(shift=shift, resolution=512 >> shift, calibration=[names[int(i)] for i in cal],
                    screening='each stored arithmetic node alone at INT16, mean Jaccard on the 8 calibration images',
                    confirmation='top 3 single nodes + top-2 combination on the 180 validation images',
                    choice=f'highest validation mean Jaccard; at most {MAX_INT16} INT16 nodes; tie -> fewer nodes',
                    candidates=candidates, threshold='p > 0.5 (q >= 1)', test_set_used=False)
    (out / 'protocol.json').write_text(json.dumps(protocol, indent=1), encoding='utf-8')

    screen_base, _ = evaluate([], cal)
    screen = []
    for n, node in enumerate(candidates):
        r, _ = evaluate([node], cal)
        r['gain'] = r['jaccard'] - screen_base['jaccard']
        screen.append(r)
        print(f'screen {n + 1}/{len(candidates)} {node}: JA {r["jaccard"]:.5f} gain {r["gain"]:+.5f}', flush=True)
    ranking = sorted(screen, key=lambda r: r['jaccard'], reverse=True)
    (out / 'screening.json').write_text(json.dumps(dict(baseline=screen_base, rows=screen), indent=1), encoding='utf-8')

    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r, _ = evaluate(nodes, va)
        confirm.append(r)
        print(f'confirm {nodes or "all INT8"}: val JA {r["jaccard"]:.5f} DI {r["dice"]:.5f}', flush=True)
    chosen = sorted(confirm, key=lambda r: (-r['jaccard'], len(r['nodes'])))[0]
    selected = chosen['nodes']
    assert len(selected) <= MAX_INT16
    (out / 'confirmation.json').write_text(json.dumps(confirm, indent=1), encoding='utf-8')

    p8, a8 = make([])          # all-INT8 ablation, kept for the final test report
    (out / 'int8_quant_plan.json').write_text(json.dumps(p8, indent=2), encoding='utf-8')
    np.savez(out / 'int8_parameters.npz', **a8)
    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    # evaluate exactly what was serialised
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}
    qt = q_threshold(plan)
    final, golden_rows = [], None
    dest = out / 'outputs'
    dest.mkdir(exist_ok=True)
    with torch.inference_mode():
        for i in np.concatenate([cal, va]):
            q, xf = inp(i)
            is_golden = int(i) == golden_id
            fp = Observer(graph, capture=is_golden)
            logits = fp.run(xf)
            gold = out / 'golden' / names[int(i)] if is_golden else None
            if gold:
                gold.mkdir(parents=True, exist_ok=True)
            result, stats = run_integer(plan, arrays, q, fp.tensors if is_golden else None, gold)
            qmap = result[0, 0].numpy()
            np.save(dest / f'{names[int(i)]}.npy', qmap.astype('int16' if plan[-1]['qmax'] > 127 else 'int8'))
            if is_golden:
                golden_rows = stats
            final.append(dict(image=names[int(i)], group='calibration' if i in cal else 'val',
                              fp32=isic_metrics(board_upsample(logits[0, 0].numpy() > 0, shift), cells[i], rows[i], cols[i]),
                              quantized=isic_metrics(board_upsample(qmap >= qt, shift), cells[i], rows[i], cols[i]),
                              changed_pixels=int(((qmap >= qt) != (logits[0, 0].numpy() > 0)).sum())))
    groups = {}
    for g in ('calibration', 'val'):
        sub = [r for r in final if r['group'] == g]
        groups[g] = dict(count=len(sub), fp32=mean_metrics([r['fp32'] for r in sub]),
                         quantized=mean_metrics([r['quantized'] for r in sub]))
    all8 = next(r for r in confirm if not r['nodes'])
    summary = dict(tag=args.tag, shift=shift, resolution=512 >> shift, selected_int16=selected,
                   groups=groups, val_all_int8=dict((kk, all8[kk]) for kk in ('jaccard', 'dice')),
                   output_scale=plan[-1]['scale'], output_integer_threshold_ge=qt,
                   golden_image=names[golden_id], golden_layer_stats=golden_rows,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'),
                   plan_sha256=sha256(out / 'quant_plan.json'), parameters_sha256=sha256(out / 'parameters.npz'),
                   arithmetic_tests=tests, per_image=final)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    v = groups['val']
    print(f"selected INT16: {selected}  output scale {plan[-1]['scale']:.6g}  q >= {qt}")
    print(f"val FP32 JA {v['fp32']['jaccard']:.5f} DI {v['fp32']['dice']:.5f} | all-INT8 JA {all8['jaccard']:.5f} | "
          f"mixed JA {v['quantized']['jaccard']:.5f} DI {v['quantized']['dice']:.5f}")


if __name__ == '__main__':
    main()
