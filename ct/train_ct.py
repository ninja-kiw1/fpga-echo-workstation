"""⑤ CT spleen: train the deployed U-Net structure (lwnet get_arch('unet'), 34,201 parameters) slice by slice.

Same network and integer rules as the other models; only the input encoding is CT-specific:
  --input rep : R = G = B = the slice (soft-tissue window)
  --input z3  : R / G / B = slices z - o, z, z + o, o = the slice closest to 5 mm (2.5D context)
  --res 128   : host 4 x 4 box average (exact integer rounding) -> 128 payload, board runs at 128
Training cases only (results/split.json); checkpoint chosen on the 7 validation cases by the mean per-case
3D Dice over the whole volume (every slice, original 512 grid, board mask upsampled nearest).
Augmentation at 512 before the box average: rotation +-10 deg, scale 0.85-1.15, shift +-8 %,
grey gain 0.9-1.1 and offset +-0.04 (about +-16 HU); no flips (left / right anatomy matters).
Sampling: half the batch from slices that contain spleen, half from all slices.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I train_ct.py --input z3 --tag z3_r128
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))                         # run with python -I (downloaded data)
sys.path.insert(0, str(ROOT.parent / 'lwnet'))        # read-only reuse: models/

import numpy as np                    # noqa: E402
import torch                          # noqa: E402
import torch.nn.functional as F       # noqa: E402

from common import RESULTS, box, load_case, load_split, neighbours, stack_input   # noqa: E402
from models.get_model import get_arch   # noqa: E402


def load_set(cases):
    """concatenate cases: img / lab [N, 512, 512], neighbour indices (global), case slices."""
    imgs, labs, prev, nxt, spans, spacing = [], [], [], [], [], []
    off = 0
    for c in cases:
        d = load_case(c)
        p, n = neighbours(len(d['img']), d['spacing'][2])
        imgs.append(d['img'])
        labs.append(d['lab'])
        prev.append(p + off)
        nxt.append(n + off)
        spans.append((off, off + len(d['img'])))
        spacing.append(d['spacing'])
        off += len(d['img'])
    return dict(img=np.concatenate(imgs), lab=np.concatenate(labs), prev=np.concatenate(prev),
                next=np.concatenate(nxt), spans=spans, spacing=spacing, cases=list(cases))


def augment(x, m, gen):
    """x (B,3,512,512) in [0,1], m (B,1,512,512) {0,1} -> augmented, x rounded to uint8 levels."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    angle = u(-10, 10) * math.pi / 180
    scale = u(0.85, 1.15)
    tx, ty = u(-0.08, 0.08), u(-0.08, 0.08)
    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.stack([torch.stack([cos, -sin, tx], 1), torch.stack([sin, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    x, m = both[:, :3], (both[:, 3:] >= 0.5).float()
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)
    x = (x * v(0.9, 1.1) + v(-0.04, 0.04)).clamp(0, 1)
    return torch.round(x * 255), m


def box_t(x255, k):
    """torch version of common.box on integer-valued floats: floor((sum + k^2/2) / k^2)."""
    if k == 1:
        return x255
    s = F.avg_pool2d(x255, k) * (k * k)
    return torch.floor((torch.round(s) + k * k // 2) / (k * k))


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


def case_inputs(data, mode, k):
    """per case: [z, 3, 512/k, 512/k] uint8 board payloads (numpy)."""
    out = []
    for (a, b), sp in zip(data['spans'], data['spacing']):
        out.append(np.ascontiguousarray(box(stack_input(data['img'][a:b], mode, sp[2]).transpose(0, 3, 1, 2), k)))
    return out


@torch.no_grad()
def predict_case(model, x_u8, device, batch=64):
    out = []
    for i in range(0, len(x_u8), batch):
        xb = torch.from_numpy(x_u8[i:i + batch]).to(device).float().div(255)
        out.append(model(xb)[:, 0].float().cpu())
    return torch.cat(out).numpy()


def evaluate(model, data, inputs, k, device):
    """per-case 3D Dice on the 512 grid (raw and largest 3D component), mask = logit > 0 upsampled nearest."""
    from common import dice3d, largest_cc3d
    model.eval()
    rows = []
    for c, (a, b), x in zip(data['cases'], data['spans'], inputs):
        lg = predict_case(model, x, device)
        m = lg > 0
        gt = data['lab'][a:b].astype(bool)
        up = lambda mm: np.repeat(np.repeat(mm, k, 1), k, 2)
        rows.append(dict(case=c, dice=dice3d(up(m), gt), dice_lcc=dice3d(up(largest_cc3d(m)), gt)))
    model.train()
    return dict(dice=float(np.mean([r['dice'] for r in rows])), dice_lcc=float(np.mean([r['dice_lcc'] for r in rows])),
                per_case=rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--input', choices=('rep', 'z3'), required=True)
    ap.add_argument('--res', type=int, default=128, choices=(128, 256))
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--fold', type=int, default=None,
                    help='stage-2 priors: train on the training cases outside fold k (cases[k::3]), log on fold k; '
                         'the fixed-schedule last.pth is what gen_priors.py uses')
    args = ap.parse_args()
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)
    k = 512 // args.res

    split = load_split()
    if args.fold is None:
        tr, va = load_set(split['train']), load_set(split['val'])
    else:
        held = split['train'][args.fold::3]
        tr, va = load_set([c for c in split['train'] if c not in held]), load_set(held)
    va_inputs = case_inputs(va, args.input, k)
    xg = torch.from_numpy(tr['img']).to(device)
    yg = torch.from_numpy(tr['lab']).to(device)
    pg = torch.from_numpy(tr['prev']).to(device)
    ng = torch.from_numpy(tr['next']).to(device)
    pos = torch.from_numpy(np.nonzero(tr['lab'].reshape(len(tr['lab']), -1).any(1))[0]).to(device)
    print(f'train {len(xg)} slices ({len(pos)} with spleen), val {len(va["img"])} slices, '
          f'input {args.input}, res {args.res}', flush=True)

    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    log, best, t0, run = [], -1.0, time.time(), 0.0
    half = args.batch // 2
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.cat([pos[torch.randint(0, len(pos), (half,), device=device, generator=gen)],
                         torch.randint(0, len(xg), (args.batch - half,), device=device, generator=gen)])
        if args.input == 'z3':
            xb = torch.stack([xg[pg[idx]], xg[idx], xg[ng[idx]]], 1)
        else:
            xb = xg[idx].unsqueeze(1).expand(-1, 3, -1, -1)
        xb = xb.float().div(255)
        mb = yg[idx].float().unsqueeze(1)
        xb, mb = augment(xb, mb, gen)
        xb = box_t(xb, k).div(255)
        mb = (F.avg_pool2d(mb, k) >= 0.5).float() if k > 1 else mb
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            g = evaluate(model, va, va_inputs, k, device)
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, **g))
            tag = ''
            if g['dice'] > best:
                best = g['dice']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'res': args.res, 'input': args.input},
                           out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val 3D Dice {g['dice']:.4f} (largest component "
                  f"{g['dice_lcc']:.4f}) min case {min(r['dice'] for r in g['per_case']):.3f} "
                  f"({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    torch.save({'model_state_dict': model.state_dict(), 'iter': args.iters, 'res': args.res, 'input': args.input},
               out / 'last.pth')
    best_row = max(log, key=lambda r: r['dice'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps({kk: v for kk, v in best_row.items()}, indent=1))


if __name__ == '__main__':
    main()
