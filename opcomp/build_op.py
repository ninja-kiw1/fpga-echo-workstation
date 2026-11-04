"""Compile an operator into a board program and prove it: float == reference, integer error, HLS bit-exact.

  1 compile (ops_library -> IR -> 5 stages x 8 channels) and build the float U-Net;
  2 float check against the NumPy reference (whole image, borders included);
  3 quantize with the project's tools: internal nodes INT16, output INT8 (the board returns int8);
    ranges of the full-resolution path come from the compiler's interval bounds (no saturation
    for ANY input), the unused low-resolution half from a calibration run;
  4 integer check: integer output vs round(reference / output scale), on random and real images;
  5 export (network.txt + params) and HLS bit-level simulator: golden node by node + 6 inputs.
Writes results/<op>/ and deploy/<op>/ under E:\\PLD\\opcomp.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe build_op.py vessel4 [more ops]
"""
import hashlib
import json
import operator
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(PLD / 'echo'))          # common (adds lwnet), export_verify_echo
sys.path.insert(0, str(ROOT))

import numpy as np                              # noqa: E402
import torch                                    # noqa: E402

import ir                                       # noqa: E402
from compiler import STAGE_BN, STAGE_MODULES, STAGES, Compiler, to_model   # noqa: E402
from export_verify_echo import COLS, SizedRunner, build_table, pack_activations   # noqa: E402
from int8_reference import Observer, build_plan, fuse_conv_affine, run_integer, self_test   # noqa: E402
from ops_library import OPS                     # noqa: E402


def sha256(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_images():
    """Random, structured and real images (uint8 HxWx3 at 128)."""
    rng = np.random.default_rng(7)
    imgs = [rng.integers(0, 256, (128, 128, 3)).astype(np.uint8) for _ in range(3)]
    yy, xx = np.mgrid[0:128, 0:128]
    imgs.append(np.stack([(xx * 2) % 256, (yy * 2) % 256, ((xx + yy) * 3) % 256], -1).astype(np.uint8))
    imgs.append(np.zeros((128, 128, 3), np.uint8))
    imgs.append(np.full((128, 128, 3), 255, np.uint8))
    imgs.append(((xx // 8 + yy // 8) % 2 * 255)[..., None].repeat(3, -1).astype(np.uint8))
    try:
        echo = np.load(PLD / 'echo' / 'data' / 'cache' / 'seq_val_img128.npy', mmap_mode='r')
        for s in (10, 400, 1200):
            imgs.append(np.stack([echo[s], echo[s + 1], echo[s + 2]], -1).astype(np.uint8))
    except FileNotFoundError:
        pass
    try:
        from PIL import Image
        f = Image.open(PLD / 'tcp_image' / 'tcp_image_ui' / 'samples' / 'im0002.ppm').convert('RGB')
        imgs.append(np.array(f.resize((128, 128), Image.LANCZOS)))
    except Exception:
        pass
    return imgs


def analytic_ranges(comp, graph):
    """max-abs per graph node on the compiled full-resolution path, from interval bounds."""
    pre, hi, (olo, ohi) = comp.bounds()
    names = {n.target: n.name for n in graph.graph.nodes if n.op == 'call_module'}
    r = {}
    for s in range(1, STAGES + 1):
        c = names[STAGE_MODULES[s - 1]]
        r[c] = max([max(abs(a), abs(b)) for a, b in pre[s]] or [0.0])
        h = max(hi[s] or [0.0])
        conv_mod = STAGE_MODULES[s - 1].rsplit('.', 1)
        r[names[conv_mod[0] + '.' + str(int(conv_mod[1]) + 1)]] = h     # the ReLU right after
        r[names[STAGE_BN[s - 1]]] = h
    r[names['final']] = max(abs(olo), abs(ohi))
    return r, (olo, ohi)


def build(name):
    out_dir = ROOT / 'results' / name
    deploy = ROOT / 'deploy' / name
    out_dir.mkdir(parents=True, exist_ok=True)
    (deploy / 'params').mkdir(parents=True, exist_ok=True)
    builder, (out_lo, out_hi) = OPS[name]
    expr = builder()
    comp = Compiler(expr).compile()
    model = to_model(comp)
    mid, half = 0.5 * (out_lo + out_hi), 0.5 * (out_hi - out_lo)
    with torch.no_grad():                               # the board outputs value - mid, int8 over [-half, half]
        model.final.bias -= mid
    graph = torch.fx.symbolic_trace(model)
    imgs = test_images()
    report = dict(op=name, doc=builder.__doc__, layout=comp.summary(), out_range=[out_lo, out_hi], out_mid=mid,
                  anchored_channels=getattr(comp, 'anchors', 0))

    # 2 float == reference
    x = np.stack(imgs).astype(np.float64) / 255
    with torch.inference_mode():
        y = model(torch.from_numpy(x).permute(0, 3, 1, 2).float())[:, 0].double().numpy()
    ref = np.stack([ir.evaluate(expr, xi) for xi in x])
    assert ref.min() >= out_lo - 1e-9 and ref.max() <= out_hi + 1e-9, 'declared output range is not a bound'
    report['float_max_abs_error'] = float(np.abs(y + mid - ref).max())

    # 3 quantize: calibration for every node, analytic bounds override the compiled path
    obs = Observer(graph)
    with torch.inference_mode():
        for xi in x:
            obs.run(torch.from_numpy(xi).permute(2, 0, 1).unsqueeze(0).float())
    ranges = dict(obs.ranges)
    ana, (olo, ohi) = analytic_ranges(comp, graph)
    ranges.update(ana)
    ranges[[n for n in graph.graph.nodes if n.op == 'output'][0].all_input_nodes[0].name] = half
    for n in graph.graph.nodes:                         # Add / Concat after the compiled stages
        if n.op == 'call_function' and n.target in (operator.add, torch.cat):
            ranges[n.name] = max(ranges[a.name] for a in n.all_input_nodes) * (
                len(n.all_input_nodes) if n.target is operator.add else 1)
    out_name = [n for n in graph.graph.nodes if n.op == 'output'][0].all_input_nodes[0].name
    bits = {n.name: 16 for n in graph.graph.nodes if n.op in ('call_module', 'call_function') and n.name != out_name}
    plan, arrays = build_plan(graph, ranges, bits)
    plan, arrays = fuse_conv_affine(graph, plan, arrays)
    (out_dir / 'quant_plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    np.savez(out_dir / 'parameters.npz', **arrays)
    plan = json.loads((out_dir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out_dir / 'parameters.npz', allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    scale = plan[-1]['scale']

    # 4 integer vs reference
    def run(img):
        with torch.inference_mode():
            q = torch.from_numpy(np.ascontiguousarray(img)).permute(2, 0, 1).unsqueeze(0)
            return run_integer(plan, arrays, q)[0][0, 0].numpy().astype(np.int64)
    ints = [run(im) for im in imgs]
    errs = np.concatenate([(iv - (r - mid) / scale).ravel() for iv, r in zip(ints, ref)])
    report['integer'] = dict(output_scale=scale, output_mid=mid, interval_bound=[olo, ohi], lsb_in_grey_levels=scale * 255,
                             mean_err_lsb=float(errs.mean()), max_abs_err_lsb=float(np.abs(errs).max()),
                             saturated=int(sum((((iv == 127) | (iv == -127)) & (np.abs((r - mid) / scale) < 125.0)).sum()
                                               for iv, r in zip(ints, ref))))

    # 5 export + HLS bit-level simulator
    npz = np.load(out_dir / 'parameters.npz')
    rows, shapes = build_table(plan, npz, deploy / 'params')
    (deploy / 'network.txt').write_text(''.join(' '.join(str(r[c]) for c in COLS) + '\n' for r in rows), encoding='ascii')
    runner = SizedRunner(deploy)
    gimg = imgs[-1]
    gdir = out_dir / 'golden'
    gdir.mkdir(exist_ok=True)
    fp = Observer(graph, capture=True)
    q = torch.from_numpy(np.ascontiguousarray(gimg)).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        fp.run(q.float() / 255)
        run_integer(plan, arrays, q, fp.tensors, gdir)
    diff = {}

    def check(node, tensor):
        r_ = np.load(gdir / f'{node.name}.npy')[0].transpose(1, 2, 0)
        diff[node.name] = int((r_.astype(np.int64) != tensor.astype(np.int64)).sum())
    runner.run(gimg, check)
    sim_bad = [i for i, im in enumerate(imgs[:6]) if not np.array_equal(runner.run(im), ints[i])]
    runner.close()
    vdir = deploy / 'images' / 'golden'
    vdir.mkdir(parents=True, exist_ok=True)
    pack_activations(gimg).astype('<i2').tofile(vdir / 'input.bin')
    report['hls_simulator'] = dict(golden_nodes=len(diff), golden_nodes_with_diff=[k for k, v in diff.items() if v],
                                   inputs_checked=6, inputs_mismatching=sim_bad)
    # pass: float == reference, HLS bit-exact, integer within 2 LSB of the reference, and no clipping
    # more than 2 LSB inside the declared range (exact numbers are in the report either way)
    report['pass'] = (report['float_max_abs_error'] < 1e-5 and not report['hls_simulator']['golden_nodes_with_diff']
                      and not sim_bad and report['integer']['saturated'] == 0
                      and report['integer']['max_abs_err_lsb'] <= 2.0)
    report['plan_sha256'] = sha256(out_dir / 'quant_plan.json')
    report['parameters_sha256'] = sha256(out_dir / 'parameters.npz')
    report['arithmetic_tests'] = self_test()
    (deploy / 'manifest.json').write_text(json.dumps(dict(
        op=name, doc=builder.__doc__, output_scale=scale, output_mid=mid,
        output='int8 logit = round((value - mid) / scale); value = logit * scale + mid',
        input='R, G, B = uint8 channels of the frame (see the op doc for their meaning)', nodes=rows,
        output_shapes={k: list(v) for k, v in shapes.items()}), indent=1), encoding='utf-8')
    (out_dir / 'report.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('%-8s %s  layout %s  float err %.1e  int err mean %+.2f max %.2f LSB (1 LSB = %.2f grey)  sim diff %s %s' % (
        name, 'PASS' if report['pass'] else 'FAIL', comp.summary()['channels_per_stage'],
        report['float_max_abs_error'], report['integer']['mean_err_lsb'], report['integer']['max_abs_err_lsb'],
        report['integer']['lsb_in_grey_levels'], report['hls_simulator']['golden_nodes_with_diff'], sim_bad), flush=True)
    return report


if __name__ == '__main__':
    torch.set_num_threads(8)
    results = [build(n) for n in (sys.argv[1:] or list(OPS))]
    sys.exit(0 if all(r['pass'] for r in results) else 1)
