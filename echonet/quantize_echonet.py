"""③ PTQ of the EchoNet LV model under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules (int8_reference.py unchanged).

  * calibration: 8 TRAIN frames (seed 20260923), board payloads (padded 128, rep / t3);
  * INT16 screening: every stored arithmetic node except the INT8 output, mean Dice on 400 TRAIN frames (seed 20260924);
  * confirmation: all-INT8, top 3 single nodes, top-2 combination on all VAL ED / ES frames; at most 2 INT16 nodes;
    best mean Dice, ties -> fewer nodes;  * TEST untouched.
Mask = integer logit >= 1 on the cropped 112 grid. Outputs results/quant/<tag>/ (+ val integer outputs, golden dumps).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I quantize_echonet.py --tag t3
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

from common import PAD, RESULTS, load_frames   # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test   # noqa: E402
from models.get_model import get_arch   # noqa: E402
from train_echonet import dice_batch, to_input   # noqa: E402

CAL_SEED, N_CAL, MAX_INT16, N_SCREEN = 20260923, 8, 2, 400


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_int(plan, arrays, x):
    out = []
    with torch.inference_mode():
        for i in range(0, len(x), 64):
            out.append(run_integer(plan, arrays, torch.from_numpy(np.ascontiguousarray(x[i:i + 64])))[0][:, 0].numpy())
    return np.concatenate(out)[:, PAD:PAD + 112, PAD:PAD + 112].astype(np.int8)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    run = RESULTS / 'train' / args.tag
    out = RESULTS / 'quant' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    tests = self_test()
    ck = torch.load(run / 'best.pth', weights_only=True, map_location='cpu')
    mode = ck['input']
    model = get_arch('unet').eval()
    model.load_state_dict(ck['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    xtr, ytr, _ = load_frames('train')
    xva, yva, mva = load_frames('val')
    rng = np.random.default_rng(CAL_SEED)
    cal = np.sort(rng.choice(len(xtr), N_CAL, replace=False))
    scr = np.sort(np.random.default_rng(CAL_SEED + 1).choice(len(xtr), N_SCREEN, replace=False))
    xcal = to_input(np.asarray(xtr[cal]), mode)
    xscr, yscr = to_input(np.asarray(xtr[scr]), mode), np.asarray(ytr[scr]).astype(bool)
    xv, yv = to_input(np.asarray(xva), mode), np.asarray(yva).astype(bool)

    observer = Observer(graph)
    with torch.inference_mode():
        for i in range(len(xcal)):
            xf = torch.from_numpy(xcal[i:i + 1]).float() / 255
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def evaluate(nodes, x, y):
        plan, arrays = make(nodes)
        return dict(nodes=list(nodes), dice=float(dice_batch(run_int(plan, arrays, x) >= 1, y).mean()))

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    base = evaluate([], xscr, yscr)
    screen = []
    for n, node in enumerate(candidates):
        r = evaluate([node], xscr, yscr)
        r['gain'] = r['dice'] - base['dice']
        screen.append(r)
        print(f"screen {n + 1}/{len(candidates)} {node}: Dice {r['dice']:.5f} gain {r['gain']:+.5f}", flush=True)
    ranking = sorted(screen, key=lambda r: r['dice'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = evaluate(nodes, xv, yv)
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: val Dice {r['dice']:.5f}", flush=True)
    selected = sorted(confirm, key=lambda r: (-round(r['dice'], 6), len(r['nodes'])))[0]['nodes']

    p8, a8 = make([])
    (out / 'int8_quant_plan.json').write_text(json.dumps(p8, indent=2), encoding='utf-8')
    np.savez(out / 'int8_parameters.npz', **a8)
    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}

    golden = 0
    gdir = out / 'golden' / f"{mva['frames'][golden]['video'][:-4]}_{mva['frames'][golden]['phase']}"
    gdir.mkdir(parents=True, exist_ok=True)
    fp = Observer(graph, capture=True)
    with torch.inference_mode():
        fp.run(torch.from_numpy(xv[golden:golden + 1]).float() / 255)
        run_integer(plan, arrays, torch.from_numpy(xv[golden:golden + 1]), fp.tensors, gdir)
    q = run_int(plan, arrays, xv)
    np.save(out / 'val_outputs.npy', q)
    with torch.inference_mode():
        lg = np.concatenate([model(torch.from_numpy(xv[i:i + 256]).float() / 255)[:, 0, PAD:PAD + 112, PAD:PAD + 112].numpy()
                             for i in range(0, len(xv), 256)])
    ph = np.array([m['phase'] for m in mva['frames']])
    dq, df = dice_batch(q >= 1, yv), dice_batch(lg > 0, yv)
    summary = dict(tag=args.tag, input=mode, selected_int16=selected, calibration=cal.tolist(), screen_frames=len(scr),
                   val_fp32=dict(dice=float(df.mean()), ed=float(df[ph == 'ED'].mean()), es=float(df[ph == 'ES'].mean())),
                   val_quantized=dict(dice=float(dq.mean()), ed=float(dq[ph == 'ED'].mean()), es=float(dq[ph == 'ES'].mean())),
                   val_all_int8=next(r for r in confirm if not r['nodes'])['dice'], screening=screen, confirmation=confirm,
                   output_scale=plan[-1]['scale'], output_integer_threshold_ge=1, golden=gdir.name, golden_index=golden,
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(f"selected INT16 {selected}; val Dice FP32 {summary['val_fp32']['dice']:.4f} | all-INT8 "
          f"{summary['val_all_int8']:.4f} | mixed {summary['val_quantized']['dice']:.4f}")


if __name__ == '__main__':
    main()
