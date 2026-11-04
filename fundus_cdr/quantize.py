"""PTQ of a part D model under lwnet INTEGER_SPEC v2 + MIXED_PRECISION_SPEC rules (same procedure as echo/quantize_echo.py).

  * calibration: 8 training images (seed 20260923), payloads of the model's own task (disc_mixed: 4 full + 4 ROI);
  * INT16 screening: every stored arithmetic node except the INT8 output, mean Dice on the calibration payloads;
  * confirmation: top 3 single nodes + top-2 combination on Chaksu VAL (train.val_inputs); at most 2 INT16 nodes;
  * the official test set is not read.
Outputs (results/quant/<tag>/): quant_plan.json, parameters.npz, golden/<node>.npy for VAL input 0,
outputs/<kind>_<i>.npy (int8 logits of every VAL input), summary.json.

    python -I quantize.py --tag disc_full_a
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
import torch                             # noqa: E402

import cdr_ref                           # noqa: E402
from common import RESULTS, load_cache   # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test   # noqa: E402
from models.get_model import get_arch    # noqa: E402
from train import val_dice, val_inputs   # noqa: E402

CAL_SEED, N_CAL = 20260923, 8


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def as_q(pay):
    """uint8 payload (128,128,3) -> uint8 tensor [1,3,128,128] and float input."""
    q = torch.from_numpy(np.ascontiguousarray(pay)).permute(2, 0, 1).unsqueeze(0).contiguous()
    return q, q.float() / 255


def cal_payloads(tr, task):
    idx = np.sort(np.random.default_rng(CAL_SEED).choice(len(tr['meta']), N_CAL, replace=False))
    out = []
    for j, i in enumerate(idx):
        im = np.asarray(tr['img512'][i])
        full = task == 'disc_full' or (task == 'disc_mixed' and j % 2 == 0)
        if full:
            out.append(cdr_ref.payload1(im))
            continue
        d = np.asarray(tr['disc512'][i])
        r, c = np.nonzero(d.any(1))[0], np.nonzero(d.any(0))[0]
        side = cdr_ref.side_for(int(max(r[-1] - r[0] + 1, c[-1] - c[0] + 1)))
        y0, x0 = cdr_ref.window(int(r[0] + r[-1] + 1) // 2, int(c[0] + c[-1] + 1) // 2, side)
        out.append(cdr_ref.payload2(im, y0, x0, side))
    return [tr['meta'][i]['name'] for i in idx], out


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
    task = ckpt['task']
    target = 'cup' if task == 'cup_roi' else 'disc'
    model = get_arch('unet').eval()
    model.load_state_dict(ckpt['model_state_dict'], strict=True)
    graph = torch.fx.symbolic_trace(model)

    tr, val = load_cache('chaksu_train'), load_cache('chaksu_val')
    cal_names, cal = cal_payloads(tr, task)
    observer = Observer(graph)
    with torch.inference_mode():
        for p in cal:
            _, xf = as_q(p)
            assert torch.equal(observer.run(xf), model(xf))
    ranges = dict(observer.ranges)

    def make(nodes):
        p, a = build_plan(graph, ranges, {n: 16 for n in nodes})
        return fuse_conv_affine(graph, p, a)

    def int_logits(plan, arrays, pays):
        with torch.inference_mode():
            return np.stack([run_integer(plan, arrays, as_q(p)[0])[0][0, 0].numpy() for p in pays])

    # calibration "Dice" needs targets: use the FP32 model's own masks (no GT leakage from VAL)
    with torch.inference_mode():
        cal_ref = [model(as_q(p)[1])[0, 0].numpy() > 0 for p in cal]

    def cal_score(nodes):
        plan, arrays = make(nodes)
        q = int_logits(plan, arrays, cal)
        ds = [2 * (a & (b >= 1)).sum() / max(a.sum() + (b >= 1).sum(), 1) for a, b in zip(cal_ref, q)]
        return float(np.mean(ds))

    vin = val_inputs(val, task)
    cells = val[f'{target}cells']

    def val_score(nodes):
        plan, arrays = make(nodes)
        r = {}
        for name, (pays, wins) in vin.items():
            r[name] = val_dice(int_logits(plan, arrays, pays) - 0.5, wins, cells, val['rows'], val['cols'])
        return dict(nodes=list(nodes), score=float(np.mean(list(r.values()))), **{f'dice_{k}': v for k, v in r.items()})

    base_plan, _ = make([])
    candidates = [s['name'] for s in base_plan[:-1] if s['op'] not in ('Input', 'MaxPool2d', 'ReLU')]
    assert base_plan[-1]['name'] == 'final'
    base = cal_score([])
    screen = []
    for n, node in enumerate(candidates):
        s = cal_score([node])
        screen.append(dict(nodes=[node], cal=s, gain=s - base))
        print(f"screen {n + 1}/{len(candidates)} {node}: cal agreement {s:.5f} gain {s - base:+.5f}", flush=True)
    ranking = sorted(screen, key=lambda r: r['cal'], reverse=True)
    options = [[]] + [r['nodes'] for r in ranking[:3]] + [ranking[0]['nodes'] + ranking[1]['nodes']]
    confirm = []
    for nodes in options:
        r = val_score(nodes)
        confirm.append(r)
        print(f"confirm {nodes or 'all INT8'}: VAL {r}", flush=True)
    chosen = sorted(confirm, key=lambda r: (-r['score'], len(r['nodes'])))[0]
    selected = chosen['nodes']

    plan, arrays = make(selected)
    (out / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out / 'parameters.npz', **arrays)
    plan = json.loads((out / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out / 'parameters.npz', allow_pickle=False) as z:
        arrays = {kk: z[kk] for kk in z.files}

    (out / 'outputs').mkdir(exist_ok=True)
    fp, qd = {}, {}
    first = next(iter(vin))
    with torch.inference_mode():
        for name, (pays, wins) in vin.items():
            lf, lq = [], []
            for i, p in enumerate(pays):
                q, xf = as_q(p)
                golden = name == first and i == 0
                obs = Observer(graph, capture=golden)
                lf.append(obs.run(xf)[0, 0].numpy())
                gdir = out / 'golden' if golden else None
                if gdir:
                    gdir.mkdir(exist_ok=True)
                    np.save(out / 'golden_input.npy', p)
                res, _ = run_integer(plan, arrays, q, obs.tensors if golden else None, gdir)
                lq.append(res[0, 0].numpy())
                np.save(out / 'outputs' / f'{name}_{i:03d}.npy', lq[-1].astype(np.int8))
            np.save(out / f'val_payloads_{name}.npy', np.stack(pays))
            fp[name] = val_dice(np.stack(lf), wins, cells, val['rows'], val['cols'])
            qd[name] = val_dice(np.stack(lq) - 0.5, wins, cells, val['rows'], val['cols'])
    summary = dict(tag=args.tag, task=task, target=target, resolution=128, selected_int16=selected,
                   calibration=cal_names, val_dice_fp32=fp, val_dice_quantized=qd,
                   val_all_int8=next(r for r in confirm if not r['nodes']), screening=screen, confirmation=confirm,
                   output_scale=plan[-1]['scale'], output_integer_threshold_ge=1, golden=f'{first}_000',
                   max_accumulator_abs_bound=max(s.get('accumulator_abs_bound', 0) for s in plan),
                   checkpoint_sha256=sha256(run / 'best.pth'), plan_sha256=sha256(out / 'quant_plan.json'),
                   parameters_sha256=sha256(out / 'parameters.npz'), arithmetic_tests=tests, test_set_used=False)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    print(f"selected INT16 {selected}; VAL Dice FP32 {fp} | quantized {qd}")


if __name__ == '__main__':
    main()
