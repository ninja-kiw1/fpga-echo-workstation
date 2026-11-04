"""RV / RA programs, step 2: train the deployed U-Net (lwnet get_arch('unet'), 34,201 parameters) on CardiacUDA.

Same recipe as CAMUS F1 / P1 1.6 (echo/train_echo.py: augment, loss, AdamW + cosine, 20k iterations, batch 16,
grey replicated to R = G = B, board tier 128 = 512[::4, ::4]); one binary program per chamber.
Cross-dataset options (10-23 evening): --aug wide (augment_wide) and --pseudo (filtered teacher labels on EchoNet /
CAMUS TRAIN frames from pseudo_label.py, a fixed fraction of every batch).
Model selection: VAL mean Dice at the native crop resolution (results/split.json). TEST untouched.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I train_rv.py --target rv --tag rv_r128
    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I train_rv.py --target rv --tag rv_w --aug wide
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common_rv import RESULTS, dice, grouped, load, to_native  # noqa: E402
from models.get_model import get_arch  # noqa: E402
from train_echo import augment, loss_fn  # noqa: E402

SHIFT = 2


def augment_wide(x, m, gen):
    """cross-dataset framing / appearance augmentation (x, m (B,1,512,512) in [0,1] / {0,1}):
    rotation +-15 deg, isotropic scale 0.65-1.25 x aspect 0.8-1.25, shift +-15 %;
    sector-edge cut (p 0.4): everything beyond a random line through the sector side (left or right, tilted like a
    sector edge) is set to 0 in the image and the label (the chamber is partly outside the sector, CAMUS-like);
    low resolution (p 0.5): average-pool by 2-3 and bilinear back up (EchoNet 112 / MJPG softness);
    brightness / contrast / gamma as echo, Gaussian noise sd 0-4 grey levels; uint8 rounding."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo  # noqa: E731
    angle = u(-15, 15) * math.pi / 180
    s = u(0.65, 1.25)
    asp = torch.exp(u(math.log(0.8), math.log(1.25)))
    sx, sy = s * asp.sqrt(), s / asp.sqrt()
    tx, ty = u(-0.15, 0.15), u(-0.15, 0.15)
    cos, sin = torch.cos(angle), torch.sin(angle)
    theta = torch.stack([torch.stack([cos / sx, -sin / sx, tx], 1), torch.stack([sin / sy, cos / sy, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    x, m = both[:, :1], (both[:, 1:] >= 0.5).float()
    H = x.shape[-1]
    yy, xx = torch.meshgrid(torch.linspace(-1, 1, H, device=dev), torch.linspace(-1, 1, H, device=dev), indexing='ij')
    cut = torch.rand(b, device=dev, generator=gen) < 0.4
    side = torch.where(torch.rand(b, device=dev, generator=gen) < 0.5, -1.0, 1.0)
    edge = u(0.25, 0.75)                     # where the edge crosses mid-height, |x| from the centre
    tilt = u(0.3, 0.7)                       # sector edges open downwards
    keep = (side.view(b, 1, 1) * xx) < (edge.view(b, 1, 1) + tilt.view(b, 1, 1) * yy)
    keep = (keep | ~cut.view(b, 1, 1)).float().unsqueeze(1)
    x, m = x * keep, m * keep
    low = torch.rand(b, device=dev, generator=gen) < 0.5
    if low.any():
        f = 2 if torch.rand(1, device=dev, generator=gen).item() < 0.5 else 3
        xl = F.interpolate(F.avg_pool2d(x[low], f, ceil_mode=True), size=(H, H), mode='bilinear', align_corners=False)
        x = x.clone()
        x[low] = xl
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)  # noqa: E731
    x = x * v(0.6, 1.4)
    mean = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - mean) * v(0.6, 1.4) + mean
    x = x.clamp(0, 1) ** v(0.6, 1.6)
    x = x + torch.randn(x.shape, device=dev, generator=gen) * v(0, 4 / 255)
    x = torch.round(x.clamp(0, 1) * 255) / 255
    return x, m


@torch.no_grad()
def predict(model, x512, device, batch=32):
    k = 1 << SHIFT
    out = []
    for i in range(0, len(x512), batch):
        xb = torch.from_numpy(np.ascontiguousarray(x512[i:i + batch, ::k, ::k])).to(device)
        out.append(model(xb.float().div(255).unsqueeze(1).expand(-1, 3, -1, -1))[:, 0].cpu())
    return torch.cat(out).numpy()


def evaluate(model, data, device):
    x, _, gts, meta = data
    model.eval()
    lg = predict(model, x, device)
    model.train()
    per = [dice(to_native(lg[i] > 0, gts[i].shape), gts[i]) for i in range(len(meta))]
    return grouped(per, meta), per


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--target', required=True, choices=('rv', 'ra'))
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=500)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--aug', default='base', choices=('base', 'wide'), help='base = echo augment; wide = augment_wide')
    ap.add_argument('--pseudo', default=None, help='pseudo_label.py npz (x512 uint8, y512 bool) mixed into batches')
    ap.add_argument('--pseudo-frac', type=float, default=0.5, help='fraction of every batch drawn from --pseudo')
    args = ap.parse_args()
    aug = augment_wide if args.aug == 'wide' else augment
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)
    xtr, ytr, _, _ = load('train', args.target)
    val = load('val', args.target)
    xg = torch.from_numpy(np.ascontiguousarray(xtr)).to(device)
    yg = torch.from_numpy(np.ascontiguousarray(ytr)).to(device)
    n_ps = 0
    if args.pseudo:
        with np.load(args.pseudo) as z:
            xp = torch.from_numpy(z['x512']).to(device)
            yp = torch.from_numpy(z['y512']).to(device)
        n_ps = int(round(args.batch * args.pseudo_frac))
        print(f'pseudo-labelled frames: {len(xp)}, {n_ps} per batch of {args.batch}', flush=True)
    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    k = 1 << SHIFT
    log, best, t0, run = [], -1.0, time.time(), 0.0
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(xg), (args.batch - n_ps,), device=device, generator=gen)
        xb, mb = xg[idx], yg[idx]
        if n_ps:
            jdx = torch.randint(0, len(xp), (n_ps,), device=device, generator=gen)
            xb, mb = torch.cat([xb, xp[jdx]]), torch.cat([mb, yp[jdx]])
        xb, mb = aug(xb.float().div(255).unsqueeze(1), mb.float().unsqueeze(1), gen)
        xb = xb[:, :, ::k, ::k].expand(-1, 3, -1, -1)
        mb = (F.avg_pool2d(mb, k) >= 0.5).float()
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            g, _ = evaluate(model, val, device)
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, **g))
            tag = ''
            if g['all'] > best:
                best = g['all']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'shift': SHIFT, 'target': args.target},
                           out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val Dice {g['all']:.4f} G {g['site_G']:.4f} R {g['site_R']:.4f} "
                  f"P5 {g['p5']:.3f} ({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['all'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
