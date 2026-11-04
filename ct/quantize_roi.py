"""⑤ stage 2: PTQ of the ROI fine model under the same lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules.

Everything runs through the real two-stage path (roi.py):
  * training cases: coarse = out-of-fold coarse results (data/cache/cvq_<case>.npy), so the windows and priors are
    realistic; calibration = 8 fine payloads of the training cases (seed 20260923): 4 from slices with spleen, 4 any;
  * INT16 screening: mean two-stage 3D Dice on 3 training cases (seed 20260924), fine model integer;
  * confirmation: all-INT8, top 3 single nodes, top-2 combination on the 7 validation cases (coarse = model 14 integer
    outputs); at most 2 INT16 nodes; best mean 3D Dice, ties -> fewer nodes;
  * test cases not used.
Outputs results/quant/<tag>/ (plans, parameters, fine integer outputs + windows per validation case, golden dumps,
summary.json).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I quantize_roi.py --tag roi_prior
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

from common import CACHE, RESULTS, dice3d, load_split   # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test   # noqa: E402
from models.get_model import get_arch   # noqa: E402
from quantize_ct import run_int   # noqa: E402
from roi import coarse_mask, combine, fine_inputs, roi_window   # noqa: E402
from train_ct import load_set   # noqa: E402

CAL_SEED, N_CAL, MAX_INT16, N_SCREEN = 20260923, 8, 2, 3


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_inputs(data, qsrc, mode):
    """per case: coarse mask, window, fine payloads [m, 3, 128, 128], label."""
    out = []
    for c, (a, b), sp in zip(data['cases'], data['spans'], data['spacing']):
        m0 = coarse_mask(qsrc(c))
        win = roi_window(m0, sp[2])
        x = fine_inputs(data['img'][a:b], m0, win, mode, sp[2]).transpose(0, 3, 1, 2).copy()
        out.append(dict(case=c, m0=m0, win=win, x=x, gt=data['lab'][a:b].astype(bool), nz=b - a))
    return out


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
    mode = ckpt['input']
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    split = load_split()
    tr = stage_inputs(load_set(split['train']), lambda c: np.load(CACHE / f'cvq_{c}.npy'), mode)
    va = stage_inputs(load_set(split['val']),
                      lambda c: np.load(RESULTS / 'quant' / 'z3_r128' / 'outputs' / f'{c}.npy'), mode)
    flat = np.concatenate([t['x'] for t in tr])
    has = np.concatenate([t['gt'][t['win'][2]:t['win'][3] + 1].reshape(len(t['x']), -1).any(1) for t in tr])
    rng = np.random.default_rng(CAL_SEED)
    cal = np.sort(np.concatenate([rng.choice(np.nonzero(has)[0], N_CAL // 2, replace=False),
                                  rng.choice(len(flat), N_CAL // 2, replace=False)]))
    screen = sorted(np.random.default_rng(CAL_SEED + 1).choice(len(tr), N_SCREEN, replace=False).tolist())

    observer = Observer(graph)
    with torch.inference_mode():
        for i in cal:
            xf = torch.from_numpy(flat[i:i + 1]).float() / 255
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def evaluate(nodes, cases):
        plan, arrays = make(nodes)
        rows = [dict(case=t['case'], dice=dice3d(combine(run_int(plan, arrays, t['x']), t['win'], t['nz'], 0.5), t['gt']))
                for t in cases]
        return dict(nodes=list(nodes), dice=float(np.mean([r['dice'] for r in rows])), per_case=rows)

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    scr_cases = [tr[i] for i in screen]
    base = evaluate([], scr_cases)
    scr = []
    for n, node in enumerate(candidates):
        r = evaluate([node], scr_cases)
        r['gain'] = r['dice'] - base['dice']
        scr.append(r)
        print(f"screen {n + 1}/{len(candidates)} {node}: two-stage 3D Dice {r['dice']:.5f} gain {r['gain']:+.5f}", flush=True)
    ranking = sorted(scr, key=lambda r: r['dice'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = evaluate(nodes, va)
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: val two-stage 3D Dice {r['dice']:.5f}", flush=True)
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

    t0 = va[0]                                   # golden: first validation case, window slice with the most spleen
    gz = int(np.argmax(t0['gt'][t0['win'][2]:t0['win'][3] + 1].reshape(len(t0['x']), -1).sum(1)))
    gname = f"{t0['case']}_roi{gz:03d}"
    gdir = out / 'golden' / gname
    gdir.mkdir(parents=True, exist_ok=True)
    fp = Observer(graph, capture=True)
    with torch.inference_mode():
        fp.run(torch.from_numpy(t0['x'][gz:gz + 1]).float() / 255)
        run_integer(plan, arrays, torch.from_numpy(t0['x'][gz:gz + 1]), fp.tensors, gdir)

    rows = []
    for t in va:
        q = run_int(plan, arrays, t['x'])
        np.save(out / 'outputs' / f"{t['case']}.npy", q)
        with torch.inference_mode():
            lg = np.concatenate([model(torch.from_numpy(t['x'][j:j + 32]).float() / 255)[:, 0].numpy()
                                 for j in range(0, len(q), 32)])
        rows.append(dict(case=t['case'], window=list(t['win']), slices=len(q), dice_single=dice3d(t['m0'], t['gt']),
                         dice_fp32=dice3d(combine(lg, t['win'], t['nz'], 0.0), t['gt']),
                         dice=dice3d(combine(q, t['win'], t['nz'], 0.5), t['gt'])))
        print(rows[-1], flush=True)
    mean = lambda key: float(np.mean([r[key] for r in rows]))
    summary = dict(tag=args.tag, input=mode, res=128, crop=256, selected_int16=selected,
                   calibration_flat_indices=cal.tolist(), screen_cases=[tr[i]['case'] for i in screen],
                   val_single_stage=mean('dice_single'), val_fp32=mean('dice_fp32'), val_quantized=mean('dice'),
                   val_all_int8=next(r for r in confirm if not r['nodes'])['dice'],
                   per_case=rows, screening=scr, confirmation=confirm, output_scale=plan[-1]['scale'],
                   output_integer_threshold_ge=1, golden=gname,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(f"selected INT16 {selected}; val two-stage 3D Dice FP32 {summary['val_fp32']:.4f} | all-INT8 "
          f"{summary['val_all_int8']:.4f} | mixed {summary['val_quantized']:.4f} (single stage {summary['val_single_stage']:.4f})")


if __name__ == '__main__':
    main()
