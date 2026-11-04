"""Part D: train the deployed U-Net structure (lwnet get_arch('unet'), 34,201 parameters) for disc / cup at 128.

Tasks (all inputs are 128 x 128 RGB payloads made exactly like cdr_ref.py, from the 512 x 512 host image):
  disc_full   payload1 = box4(img512), target disc                                (stage 1: locate the disc)
  disc_roi    payload2 = box_k(window around the disc), target disc               (stage 2)
  cup_roi     payload2, target cup                                                 (stage 2)
  disc_mixed  half payload1 / half payload2, target disc (one program for both stages)
ROI windows during training come from the GT disc box with jitter that covers the stage-1 error:
  centre +- JIT_C * size, size x [1 - JIT_S, 1 + JIT_S], then cdr_ref.side_for(size) picks k.
Augmentation on the 512 image before the payload: rotation +-15 deg, scale 0.9-1.1, shift +-5 %, horizontal flip
(left / right eyes mirror), brightness / contrast / gamma / per-channel gain, uint8 rounding.
Selection: Chaksu VAL (our split of the official training set, prep_chaksu.py). The official test set is not read.

    python -I train.py --task disc_full --tag disc_full_a
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np                       # noqa: E402
import torch                             # noqa: E402
import torch.nn.functional as F          # noqa: E402

import cdr_ref                           # noqa: E402
from common import RESULTS, load_cache   # noqa: E402
from models.get_model import get_arch    # noqa: E402

JIT_C, JIT_S = 0.15, 0.15


def augment(x, m, gen):
    """x (B,3,512,512) in [0,1], m (B,2,512,512) {0,1} (disc, cup) -> same, geometric + photometric."""
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    angle = u(-15, 15) * math.pi / 180
    scale = u(0.9, 1.1)
    flip = torch.where(torch.rand(b, device=dev, generator=gen) < 0.5, -1.0, 1.0)
    tx, ty = u(-0.05, 0.05), u(-0.05, 0.05)
    cos, sin = torch.cos(angle) / scale, torch.sin(angle) / scale
    theta = torch.stack([torch.stack([cos * flip, -sin, tx], 1), torch.stack([sin * flip, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    both = F.grid_sample(torch.cat([x, m], 1), grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    x, m = both[:, :3], (both[:, 3:] >= 0.5).float()
    v = lambda lo, hi: u(lo, hi).view(b, 1, 1, 1)
    x = x * v(0.75, 1.25) * torch.stack([u(0.9, 1.1), u(0.9, 1.1), u(0.9, 1.1)], 1).view(b, 3, 1, 1)
    mean = x.mean(dim=(1, 2, 3), keepdim=True)
    x = (x - mean) * v(0.75, 1.3) + mean
    x = x.clamp(0, 1) ** v(0.75, 1.35)
    return torch.round(x.clamp(0, 1) * 255), m          # x now integer-valued 0..255


def box_t(x, k):
    """integer-valued (B,C,H,W) -> k x k mean, round half up == cdr_ref.box."""
    if k == 1:
        return x
    s = F.avg_pool2d(x, k) * (k * k)
    return torch.floor((s + (k * k) // 2) / (k * k))


def payloads(x, m, task_roi, gen, target):
    """x (B,3,512,512) integer-valued, m (B,2,512,512) -> (B,3,128,128) in [0,1], (B,1,128,128) targets."""
    ti = 0 if target == 'disc' else 1
    xs, ys = [], []
    for i in range(x.shape[0]):
        if not task_roi[i]:
            xs.append(box_t(x[i:i + 1], 4))
            ys.append((F.avg_pool2d(m[i:i + 1, ti:ti + 1], 4) >= 0.5).float())
            continue
        r = torch.nonzero(m[i, 0].any(1)).flatten()
        c = torch.nonzero(m[i, 0].any(0)).flatten()
        if len(r) == 0:                                     # disc rotated out of the image: random window
            cy = cx = 256
            size = 96
        else:
            r0, r1, c0, c1 = int(r[0]), int(r[-1]), int(c[0]), int(c[-1])
            size = max(r1 - r0 + 1, c1 - c0 + 1)
            j = (torch.rand(3, generator=gen, device=x.device) * 2 - 1).tolist()
            cy = int(round((r0 + r1 + 1) / 2 + j[0] * JIT_C * size))
            cx = int(round((c0 + c1 + 1) / 2 + j[1] * JIT_C * size))
            size = int(round(size * (1 + j[2] * JIT_S)))
        side = cdr_ref.side_for(size)
        y0, x0 = cdr_ref.window(cy, cx, side)
        k = side // 128
        xs.append(box_t(x[i:i + 1, :, y0:y0 + side, x0:x0 + side], k))
        ys.append((F.avg_pool2d(m[i:i + 1, ti:ti + 1, y0:y0 + side, x0:x0 + side], k) >= 0.5).float())
    return torch.cat(xs) / 255, torch.cat(ys)


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


@torch.no_grad()
def predict(model, pay, device, batch=64):
    """uint8 payloads (N,128,128,3) -> float logits (N,128,128)."""
    out = []
    for i in range(0, len(pay), batch):
        xb = torch.from_numpy(np.ascontiguousarray(pay[i:i + batch])).to(device).permute(0, 3, 1, 2).float() / 255
        out.append(model(xb)[:, 0].cpu())
    return torch.cat(out).numpy()


def val_inputs(val, task):
    """Deterministic VAL payloads: full images, or GT-centred windows (no jitter) for the ROI tasks."""
    full = np.stack([cdr_ref.payload1(im) for im in val['img512']])
    if task == 'disc_full':
        return dict(full=(full, None))
    rois, wins = [], []
    for i, im in enumerate(val['img512']):
        d = np.asarray(val['disc512'][i])
        r, c = np.nonzero(d.any(1))[0], np.nonzero(d.any(0))[0]
        size = max(r[-1] - r[0] + 1, c[-1] - c[0] + 1)
        side = cdr_ref.side_for(int(size))
        y0, x0 = cdr_ref.window(int(r[0] + r[-1] + 1) // 2, int(c[0] + c[-1] + 1) // 2, side)
        rois.append(cdr_ref.payload2(im, y0, x0, side))
        wins.append((y0, x0, side))
    out = dict(roi=(np.stack(rois), wins))
    if task == 'disc_mixed':
        out['full'] = (full, None)
    return out


def val_dice(logits, wins, cells, rows, cols):
    """Mean original-resolution Dice of the thresholded (logit > 0) mask pasted on the 512 grid."""
    from common import seg_metrics
    ds = []
    for i, q in enumerate(logits):
        canvas = np.zeros((512, 512), bool)
        if wins is None:
            canvas[:] = np.repeat(np.repeat(q > 0, 4, 0), 4, 1)
        else:
            y0, x0, side = wins[i]
            k = side // 128
            canvas[y0:y0 + side, x0:x0 + side] = np.repeat(np.repeat(q > 0, k, 0), k, 1)
        ds.append(seg_metrics(canvas, cells[i], rows[i], cols[i])['dice'])
    return float(np.mean(ds))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--task', required=True, choices=('disc_full', 'disc_roi', 'cup_roi', 'disc_mixed'))
    ap.add_argument('--tag', required=True)
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args()
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    device = torch.device('cuda')
    gen = torch.Generator(device=device).manual_seed(args.seed)
    target = 'cup' if args.task == 'cup_roi' else 'disc'

    tr, val = load_cache('chaksu_train'), load_cache('chaksu_val')
    xg = torch.from_numpy(np.ascontiguousarray(tr['img512'])).to(device)                       # uint8 N,512,512,3
    mg = torch.from_numpy(np.stack([np.asarray(tr['disc512']), np.asarray(tr['cup512'])], 1)).to(device)
    vin = val_inputs(val, args.task)
    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    p_roi = dict(disc_full=0.0, disc_roi=1.0, cup_roi=1.0, disc_mixed=0.5)[args.task]
    log, best, t0, run = [], -1.0, time.time(), 0.0
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(xg), (args.batch,), device=device, generator=gen)
        xb = xg[idx].permute(0, 3, 1, 2).float() / 255
        mb = mg[idx].float()
        xb, mb = augment(xb, mb, gen)
        roi = (torch.rand(args.batch, generator=gen, device=device) < p_roi).tolist()
        xp, yp = payloads(xb, mb, roi, gen, target)
        loss = loss_fn(model(xp), yp)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            model.eval()
            row = dict(iter=it, loss=run, seconds=time.time() - t0)
            for name, (pay, wins) in vin.items():
                cells = val[f'{target}cells']
                row[f'dice_{name}'] = val_dice(predict(model, pay, device), wins, cells, val['rows'], val['cols'])
            model.train()
            row['score'] = float(np.mean([row[k] for k in row if k.startswith('dice_')]))
            log.append(row)
            tag = ''
            if row['score'] > best:
                best = row['score']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'task': args.task}, out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} " + ' '.join(f"{k} {v:.4f}" for k, v in row.items()
                                                                       if k.startswith('dice_')) +
                  f" ({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['score'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
