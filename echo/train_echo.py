"""F1: train the deployed U-Net structure (lwnet get_arch('unet')) on CAMUS LV cavity.

Same architecture and deployment chain as the fundus / skin models; the
3 input channels carry grey replicated (R=G=B) -- the single-frame baseline.
Training: official 400 training patients, ED + ES frames of both views.
Model selection: official 50 validation patients only. Test set untouched.

Augmentation keeps echo geometry plausible: no flips (view conventions),
rotation +-15 deg, scale 0.85-1.15, shift +-8 %, brightness / contrast / gamma,
then uint8 rounding and the board's stride subsampling.

    python train_echo.py --shift 1 --tag lv_r256
"""
import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn.functional as F

from common import RESULTS, board_upsample, grouped, load_cache, seg_metrics
from models.get_model import get_arch


def augment(x, m, gen):
    """x (B,1,512,512) in [0,1], m (B,1,512,512) in {0,1}."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    angle = u(-15, 15) * math.pi / 180
    scale = u(0.85, 1.15)
    tx, ty = u(-0.08, 0.08), u(-0.08, 0.08)
    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.stack([torch.stack([cos, -sin, tx], 1), torch.stack([sin, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    x, m = both[:, :1], (both[:, 1:] >= 0.5).float()
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)
    x = x * v(0.7, 1.3)
    mean = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - mean) * v(0.7, 1.3) + mean
    x = x.clamp(0, 1) ** v(0.7, 1.4)
    x = torch.round(x.clamp(0, 1) * 255) / 255
    return x, m


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


@torch.no_grad()
def predict_logits(model, x512, shift, device, batch=32):
    k = 1 << shift
    out = []
    for i in range(0, len(x512), batch):
        xb = torch.from_numpy(np.ascontiguousarray(x512[i:i + batch, ::k, ::k])).to(device)
        xb = xb.float().div(255).unsqueeze(1).expand(-1, 3, -1, -1)
        out.append(model(xb)[:, 0].cpu())
    return torch.cat(out).numpy()


def evaluate(model, data, shift, device):
    x, _, cells, rows, cols, meta = data
    model.eval()
    logits = predict_logits(model, x, shift, device)
    model.train()
    per = [seg_metrics(board_upsample(logits[i] > 0, shift), cells[i], rows[i], cols[i]) for i in range(len(meta))]
    return grouped(per, meta), per


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--shift', type=int, default=1)
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=500)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--target', default='lv', choices=('lv', 'la', 'epi'),
                    help='lv = F1 cavity; la / epi = P1 1.6 (prep_camus_targets.py)')
    args = ap.parse_args()
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)

    xtr, ytr, *_ = load_cache('train', args.target)
    val = load_cache('val', args.target)
    xg = torch.from_numpy(np.ascontiguousarray(xtr)).to(device)
    yg = torch.from_numpy(np.ascontiguousarray(ytr)).to(device)
    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    k = 1 << args.shift
    log, best, t0, run = [], -1.0, time.time(), 0.0
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(xg), (args.batch,), device=device, generator=gen)
        xb = xg[idx].float().div(255).unsqueeze(1)
        mb = yg[idx].float().unsqueeze(1)
        xb, mb = augment(xb, mb, gen)
        xb = xb[:, :, ::k, ::k].expand(-1, 3, -1, -1)
        mb = (F.avg_pool2d(mb, k) >= 0.5).float() if k > 1 else mb
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            g, _ = evaluate(model, val, args.shift, device)
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, **g))
            tag = ''
            if g['all'] > best:
                best = g['all']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'shift': args.shift}, out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val Dice all {g['all']:.4f} good+med {g['good+medium']:.4f} "
                  f"poor {g.get('quality=Poor', float('nan')):.4f} ED {g['phase=ED']:.4f} ES {g['phase=ES']:.4f} "
                  f"({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['all'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
