"""⑤ CT spleen: PTQ under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules (int8_reference.py unchanged).

Same procedure as the echo / skin models, adapted to volumes:
  * calibration: 8 training slices (seed 20260923): 4 drawn from slices that contain spleen, 4 from all slices;
  * INT16 screening: every stored arithmetic node except the INT8 output, mean per-case 3D Dice on
    3 training cases (whole volumes, seed 20260924);
  * confirmation: all-INT8, top 3 single nodes, top-2 combination on the 7 validation cases; at most 2 INT16
    nodes; best mean 3D Dice wins, ties -> fewer INT16 nodes;
  * the test cases are not used here.
Input = exactly the board payload (common.stack_input + common.box), mask = integer logit >= 1.
Outputs: results/quant/<tag>/ (plans, parameters, val integer outputs per case, golden node dumps, summary.json).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I quantize_ct.py --tag z3_r128
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402

from common import RESULTS, dice3d, largest_cc3d, load_split   # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test   # noqa: E402
from models.get_model import get_arch   # noqa: E402
from train_ct import case_inputs, load_set   # noqa: E402

CAL_SEED, N_CAL, MAX_INT16, N_SCREEN = 20260923, 8, 2, 3
BATCH = 32


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_int(plan, arrays, x_u8):
    """[z, 3, h, w] uint8 -> [z, h, w] int8 integer logits."""
    out = []
    with torch.inference_mode():
        for i in range(0, len(x_u8), BATCH):
            out.append(run_integer(plan, arrays, torch.from_numpy(x_u8[i:i + BATCH]))[0][:, 0].numpy())
    return np.concatenate(out).astype(np.int8)


def case_dice(mask, gt, k):
    up = np.repeat(np.repeat(mask, k, 1), k, 2)
    return dice3d(up, gt), dice3d(np.repeat(np.repeat(largest_cc3d(mask), k, 1), k, 2), gt)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    run = RESULTS / 'train' / args.tag
    out = RESULTS / 'quant' / args.tag
    (out / 'outputs').mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    ckpt = torch.load(run / 'best.pth', weights_only=True, map_location='cpu')
    mode, res = ckpt['input'], int(ckpt['res'])
    k = 512 // res
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    split = load_split()
    tr, va = load_set(split['train']), load_set(split['val'])
    tr_in, va_in = case_inputs(tr, mode, k), case_inputs(va, mode, k)
    flat = np.concatenate(tr_in)
    has = np.nonzero(tr['lab'].reshape(len(tr['lab']), -1).any(1))[0]
    rng = np.random.default_rng(CAL_SEED)
    cal = np.sort(np.concatenate([rng.choice(has, N_CAL // 2, replace=False),
                                  rng.choice(len(flat), N_CAL // 2, replace=False)]))
    screen_cases = sorted(np.random.default_rng(CAL_SEED + 1).choice(len(tr['cases']), N_SCREEN, replace=False).tolist())

    observer = Observer(graph)
    with torch.inference_mode():
        for i in cal:
            xf = torch.from_numpy(flat[i:i + 1]).float() / 255
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def evaluate(nodes, data, inputs, ids):
        plan, arrays = make(nodes)
        rows = []
        for i in ids:
            a, b = data['spans'][i]
            d, dl = case_dice(run_int(plan, arrays, inputs[i]) >= 1, data['lab'][a:b].astype(bool), k)
            rows.append(dict(case=data['cases'][i], dice=d, dice_lcc=dl))
        return dict(nodes=list(nodes), dice=float(np.mean([r['dice'] for r in rows])),
                    dice_lcc=float(np.mean([r['dice_lcc'] for r in rows])), per_case=rows)

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    base = evaluate([], tr, tr_in, screen_cases)
    screen = []
    for n, node in enumerate(candidates):
        r = evaluate([node], tr, tr_in, screen_cases)
        r['gain'] = r['dice'] - base['dice']
        screen.append(r)
        print(f"screen {n + 1}/{len(candidates)} {node}: 3D Dice {r['dice']:.5f} gain {r['gain']:+.5f}", flush=True)
    ranking = sorted(screen, key=lambda r: r['dice'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = evaluate(nodes, va, va_in, range(len(va['cases'])))
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: val 3D Dice {r['dice']:.5f} (largest component {r['dice_lcc']:.5f})",
              flush=True)
    chosen = sorted(confirm, key=lambda r: (-round(r['dice'], 6), len(r['nodes'])))[0]
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

    # golden: first validation case, the slice with the largest spleen area -> every node dumped
    a0, b0 = va['spans'][0]
    gz = int(np.argmax(va['lab'][a0:b0].reshape(b0 - a0, -1).sum(1)))
    gname = f"{va['cases'][0]}_z{gz:03d}"
    gdir = out / 'golden' / gname
    gdir.mkdir(parents=True, exist_ok=True)
    xg = va_in[0][gz:gz + 1]
    fp = Observer(graph, capture=True)
    with torch.inference_mode():
        fp.run(torch.from_numpy(xg).float() / 255)
        run_integer(plan, arrays, torch.from_numpy(xg), fp.tensors, gdir)

    rows = []
    for i, c in enumerate(va['cases']):
        a, b = va['spans'][i]
        gt = va['lab'][a:b].astype(bool)
        q = run_int(plan, arrays, va_in[i])
        np.save(out / 'outputs' / f'{c}.npy', q)
        with torch.inference_mode():
            lg = np.concatenate([model(torch.from_numpy(va_in[i][j:j + BATCH]).float() / 255)[:, 0].numpy()
                                 for j in range(0, len(q), BATCH)])
        d_fp, dl_fp = case_dice(lg > 0, gt, k)
        d_q, dl_q = case_dice(q >= 1, gt, k)
        sp = va['spacing'][i]
        ml = sp[0] * sp[1] * sp[2] / 1000
        rows.append(dict(case=c, dice_fp32=d_fp, dice_fp32_lcc=dl_fp, dice=d_q, dice_lcc=dl_q,
                         gt_ml=round(float(gt.sum()) * ml, 1),
                         pred_ml_lcc=round(float(largest_cc3d(q >= 1).sum()) * k * k * ml, 1)))
        print(rows[-1], flush=True)
    mean = lambda key: float(np.mean([r[key] for r in rows]))
    summary = dict(tag=args.tag, input=mode, res=res, selected_int16=selected,
                   calibration_flat_indices=cal.tolist(), screen_cases=[tr['cases'][i] for i in screen_cases],
                   val_fp32=dict(dice=mean('dice_fp32'), dice_lcc=mean('dice_fp32_lcc')),
                   val_quantized=dict(dice=mean('dice'), dice_lcc=mean('dice_lcc')),
                   val_all_int8={kk: next(r for r in confirm if not r['nodes'])[kk] for kk in ('dice', 'dice_lcc')},
                   per_case=rows, screening=screen, confirmation=confirm, output_scale=plan[-1]['scale'],
                   output_integer_threshold_ge=1, golden=gname,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(f"selected INT16 {selected}; val 3D Dice FP32 {summary['val_fp32']['dice']:.4f} | all-INT8 "
          f"{summary['val_all_int8']['dice']:.4f} | mixed {summary['val_quantized']['dice']:.4f} "
          f"(largest component {summary['val_quantized']['dice_lcc']:.4f})")


if __name__ == '__main__':
    main()
