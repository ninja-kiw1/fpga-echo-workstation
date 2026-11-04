"""③ EchoNet-Dynamic: train the deployed U-Net (lwnet get_arch('unet'), 34,201 parameters) on the LV of ED / ES frames.

Input = exactly the board payload: grey 112 x 112 frames zero-padded to 128 (common.pad128); output cropped back to 112.
  --input rep : R = G = B = frame t
  --input t3  : R / G / B = frames t - d, t, t + d (d ~ 40 ms, common.temporal_offset)
TRAIN split only; checkpoint = mean Dice over the VAL ED + ES frames (native 112 grid, logit > 0). TEST untouched.
Augmentation on the padded 128 canvas: rotation +-15 deg, scale 0.85-1.15, shift +-8 %, brightness / contrast / gamma,
then uint8 rounding; no flips.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I train_echonet.py --input t3 --tag t3
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np                    # noqa: E402
import torch                          # noqa: E402
import torch.nn.functional as F       # noqa: E402

from common import PAD, RESULTS, load_frames   # noqa: E402
from models.get_model import get_arch   # noqa: E402


def to_input(img3, mode):
    """[B, 3, 112, 112] uint8 (t - d, t, t + d) -> [B, 3, 128, 128] uint8 board payload."""
    x = img3 if mode == 't3' else np.repeat(img3[:, 1:2], 3, axis=1)
    return np.pad(x, ((0, 0), (0, 0), (PAD, PAD), (PAD, PAD)))


def augment(x, m, gen):
    """x (B,3,128,128) in [0,1], m (B,1,128,128) {0,1}."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    angle = u(-15, 15) * math.pi / 180
    scale = u(0.85, 1.15)
    tx, ty = u(-0.08, 0.08), u(-0.08, 0.08)
    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.stack([torch.stack([cos, -sin, tx], 1), torch.stack([sin, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    x, m = both[:, :3], (both[:, 3:] >= 0.5).float()
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)
    x = x * v(0.7, 1.3)
    mean = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - mean) * v(0.7, 1.3) + mean
    x = x.clamp(0, 1) ** v(0.7, 1.4)
    return torch.round(x.clamp(0, 1) * 255) / 255, m


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


@torch.no_grad()
def predict(model, x128_u8, device, batch=256):
    """[B, 3, 128, 128] uint8 -> [B, 112, 112] float logits (cropped)."""
    out = []
    for i in range(0, len(x128_u8), batch):
        xb = torch.from_numpy(np.ascontiguousarray(x128_u8[i:i + batch])).to(device).float().div(255)
        out.append(model(xb)[:, 0, PAD:PAD + 112, PAD:PAD + 112].float().cpu())
    return torch.cat(out).numpy()


def dice_batch(pred, gt):
    p, g = pred.reshape(len(pred), -1), gt.reshape(len(gt), -1)
    inter = (p & g).sum(1)
    s = p.sum(1) + g.sum(1)
    return np.where(s > 0, 2 * inter / np.maximum(s, 1), 1.0)


def evaluate(model, xv, yv, meta, device):
    model.eval()
    d = dice_batch(predict(model, xv, device) > 0, yv.astype(bool))
    model.train()
    ph = np.array([m['phase'] for m in meta])
    return dict(dice=float(d.mean()), ed=float(d[ph == 'ED'].mean()), es=float(d[ph == 'ES'].mean()),
                p05=float(np.percentile(d, 5)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--input', choices=('rep', 't3'), required=True)
    ap.add_argument('--iters', type=int, default=30000)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)

    xtr, ytr, mtr = load_frames('train')
    xva, yva, mva = load_frames('val')
    xv = to_input(np.asarray(xva), args.input)
    yv = np.asarray(yva)
    xg = torch.from_numpy(to_input(np.asarray(xtr), args.input)).to(device)
    yg = torch.from_numpy(np.pad(np.asarray(ytr), ((0, 0), (PAD, PAD), (PAD, PAD)))).to(device)
    print(f"train {len(xg)} frames ({len(xg) // 2} videos), val {len(xv)} frames, input {args.input}", flush=True)

    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    log, best, t0, run = [], -1.0, time.time(), 0.0
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(xg), (args.batch,), device=device, generator=gen)
        xb, mb = augment(xg[idx].float().div(255), yg[idx].float().unsqueeze(1), gen)
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            g = evaluate(model, xv, yv, mva['frames'], device)
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, **g))
            tag = ''
            if g['dice'] > best:
                best = g['dice']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'input': args.input}, out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val Dice {g['dice']:.4f} ED {g['ed']:.4f} ES {g['es']:.4f} "
                  f"p5 {g['p05']:.3f} ({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['dice'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
