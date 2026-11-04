"""Train the deployed U-Net structure (lwnet get_arch('unet'), 8/16/32) on ISIC 2016.

The network is byte-for-byte the same architecture as the fundus model, so the
FPGA layer table keeps its shape; only weights change.

Inputs reproduce the deployment chain: host LANCZOS stretch to 512x512
(cached by prep_data.py) -> optional board stride subsampling to 512>>shift.
Augmentation happens on the 512x512 image before subsampling.
Model selection uses only the 180-image validation part of the official
training set; the official test set is not touched here.

    python train_skin.py --shift 2 --tag r128
"""
import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn.functional as F

from common import RESULTS, board_upsample, isic_metrics, load_cells, load_split, mean_metrics, train_val_indices
from models.get_model import get_arch


def augment(x, m, gen):
    """x (B,3,512,512) in [0,1], m (B,1,512,512) in {0,1}; GPU, random per sample."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    angle = u(-math.pi, math.pi)
    scale = u(0.8, 1.2)
    flip = torch.where(torch.rand(b, device=dev, generator=gen) < 0.5, -1.0, 1.0)
    affine = torch.rand(b, device=dev, generator=gen) < 0.7
    # the rest only get exact rot90 / flips (no interpolation at all)
    k90 = torch.randint(0, 4, (b,), device=dev, generator=gen).float() * (math.pi / 2)
    angle = torch.where(affine, angle, k90)
    scale = torch.where(affine, scale, torch.ones_like(scale))
    tx, ty = u(-0.1, 0.1) * affine, u(-0.1, 0.1) * affine
    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.stack([torch.stack([cos * flip, -sin, tx], 1), torch.stack([sin * flip, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='reflection', align_corners=False)
    x, m = both[:, :3], (both[:, 3:] >= 0.5).float()
    # photometric: brightness, contrast, saturation, gamma, per-channel gain
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)
    x = x * v(0.75, 1.25)
    mean = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - mean) * v(0.75, 1.25) + mean
    gray = x.mean(dim=1, keepdim=True)
    x = (x - gray) * v(0.7, 1.3) + gray
    x = x.clamp(0, 1) ** v(0.8, 1.25)
    x = x * (torch.rand(b, 3, 1, 1, device=dev, generator=gen) * 0.2 + 0.9)
    x = torch.round(x.clamp(0, 1) * 255) / 255     # deployed input is uint8
    return x, m


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


@torch.no_grad()
def predict_logits(model, x512_u8, shift, device, batch=16):
    """Deployment-faithful FP32 logits at 512>>shift, input uint8 NHWC 512x512."""
    k = 1 << shift
    out = []
    for i in range(0, len(x512_u8), batch):
        xb = torch.from_numpy(np.ascontiguousarray(x512_u8[i:i + batch, ::k, ::k])).to(device)
        out.append(model(xb.permute(0, 3, 1, 2).float() / 255)[:, 0].cpu())
    return torch.cat(out).numpy()


def evaluate(model, x, cells, rows, cols, idx, shift, device):
    model.eval()
    logits = predict_logits(model, x[idx], shift, device)
    per = [isic_metrics(board_upsample(logits[j] > 0, shift), cells[i], rows[i], cols[i])
           for j, i in enumerate(idx)]
    model.train()
    return mean_metrics(per), per


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--shift', type=int, default=2, help='board resolution tier: 512>>shift')
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--wd', type=float, default=1e-4)
    ap.add_argument('--eval-every', type=int, default=500)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()

    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)

    x, y, meta = load_split('train')
    cells, rows, cols = load_cells('train')
    tr, va = train_val_indices()
    (RESULTS / 'split.json').write_text(json.dumps(dict(
        seed=20261007, train=[meta[i]['name'] for i in tr], val=[meta[i]['name'] for i in va],
        note='Both parts come from the official 900-image training set; the 379 official test images are separate.'),
        indent=1), encoding='utf-8')
    xg = torch.from_numpy(np.ascontiguousarray(x[tr])).to(device)        # uint8 NHWC
    yg = torch.from_numpy(np.ascontiguousarray(y[tr])).to(device)

    model = get_arch('unet').to(device)
    n_params = sum(p.numel() for p in model.parameters())
    assert n_params == 34201, n_params
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd)
    warm = 500
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    k = 1 << args.shift
    log, best, t0, run = [], -1.0, time.time(), 0.0
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(tr), (args.batch,), device=device, generator=gen)
        xb = xg[idx].permute(0, 3, 1, 2).float() / 255
        mb = yg[idx].unsqueeze(1).float()
        xb, mb = augment(xb, mb, gen)
        xb = xb[:, :, ::k, ::k]
        mb = (F.avg_pool2d(mb, k) >= 0.5).float() if k > 1 else mb
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            m, _ = evaluate(model, x, cells, rows, cols, va, args.shift, device)
            row = dict(iter=it, loss=run, lr=sched.get_last_lr()[0], seconds=time.time() - t0, **m)
            log.append(row)
            tag = ''
            if m['jaccard'] > best:
                best = m['jaccard']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'shift': args.shift}, out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val JA {m['jaccard']:.4f} DI {m['dice']:.4f} "
                  f"TJA {m['thresholded_jaccard']:.4f} ({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    torch.save({'model_state_dict': model.state_dict(), 'iter': args.iters, 'shift': args.shift}, out / 'last.pth')
    best_row = max(log, key=lambda r: r['jaccard'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), params=n_params, best=best_row,
                                                      last=log[-1]), indent=1), encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
