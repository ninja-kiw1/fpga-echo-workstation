"""⑤ stage 2: train the ROI fine model -- the same deployed U-Net (34,201 parameters), 128 tier, on 256 x 256 windows
2 x 2 box-averaged to 128 (see roi.py and the README "第二阶段").

  --mode roi_z3     R / G / B = window of slices z-o / z / z+o
  --mode roi_prior  R = window of slice z, G = coarse mask (out-of-fold, gen_priors.py), B = mean of slices z-o, z+o
Samples: window centre = the label's whole-volume bounding-box centre + uniform jitter +-24 px (the coarse result is
within 6 px on validation); slices within the label's range +- 3 neighbour offsets, 70 % with spleen / 30 % without;
rotation +-10 deg about the window centre, scale 0.9-1.1, grey gain 0.9-1.1 and offset +-0.04 on image channels.
Crop + augmentation are one bilinear resampling of the full 512 slice (exact pixels when no augmentation applies).
Checkpoint: mean 3D Dice of the full two-stage pipeline on the 7 validation cases (coarse = model 14 integer outputs,
fine = this model in FP32, float logits > 0).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I train_roi.py --mode roi_prior --tag roi_prior
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

from common import CACHE, RESULTS, dice3d, load_split, neighbour_offset   # noqa: E402
from models.get_model import get_arch   # noqa: E402
from roi import CROP, coarse_mask, combine, fine_inputs, roi_window   # noqa: E402
from train_ct import box_t, load_set, loss_fn, predict_case   # noqa: E402

JITTER, ZEXT, POS_FRAC = 24, 3, 0.7


def slice_tables(data, cvq):
    """per-slice window base (label bbox centre - 128), positive / margin slice indices, coarse priors."""
    n = len(data['img'])
    ybase, xbase = np.zeros(n, np.int64), np.zeros(n, np.int64)
    pos, marg, priors = [], [], []
    for c, (a, b), sp in zip(data['cases'], data['spans'], data['spacing']):
        lab = data['lab'][a:b].astype(bool)
        has = lab.reshape(b - a, -1).any(1)
        zs = np.nonzero(has)[0]
        r, q = np.nonzero(lab.any(0))
        ybase[a:b] = (int(r.min()) + int(r.max()) + 1) // 2 - CROP // 2
        xbase[a:b] = (int(q.min()) + int(q.max()) + 1) // 2 - CROP // 2
        o = neighbour_offset(sp[2])
        for z in range(max(0, zs.min() - ZEXT * o), min(b - a - 1, zs.max() + ZEXT * o) + 1):
            (pos if has[z] else marg).append(a + z)
        if cvq:
            priors.append(coarse_mask(np.load(CACHE / f'cvq_{c}.npy')).astype(np.uint8))
    return ybase, xbase, np.array(pos), np.array(marg), (np.concatenate(priors) if cvq else None)


def render(src, y0, x0, ang, scl, gain, off, prior_mode):
    """src (B, 4, 512, 512): 3 input channels in [0, 1] + label. One bilinear resampling = crop of the 256 window at
    (y0, x0) + rotation about its centre + scale; then the payload's 2 x 2 box average. -> (x (B,3,128,128), label)."""
    b = src.shape[0]
    s = 0.5 / scl
    theta = torch.stack([torch.stack([s * torch.cos(ang), -s * torch.sin(ang), (x0 + CROP / 2) / 256 - 1], 1),
                         torch.stack([s * torch.sin(ang), s * torch.cos(ang), (y0 + CROP / 2) / 256 - 1], 1)], 1)
    grid = F.affine_grid(theta, (b, 4, CROP, CROP), align_corners=False)
    w = F.grid_sample(src, grid, mode='bilinear', padding_mode='zeros', align_corners=False)
    gain, off = gain.view(b, 1, 1, 1), off.view(b, 1, 1, 1)
    chans = []
    for ch in range(3):
        if prior_mode and ch == 1:          # prior: binary, 2 x 2 mean >= 0.5 -> 1
            chans.append((F.avg_pool2d((w[:, 1:2] >= 0.5).float(), 2) >= 0.5).float())
        else:
            v = torch.round((w[:, ch:ch + 1] * gain + off).clamp(0, 1) * 255)
            chans.append(box_t(v, 2) / 255)
    return torch.cat(chans, 1), (F.avg_pool2d((w[:, 3:4] >= 0.5).float(), 2) >= 0.5).float()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mode', choices=('roi_z3', 'roi_prior'), required=True)
    ap.add_argument('--iters', type=int, default=20000)
    ap.add_argument('--batch', type=int, default=16)
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
    prior_mode = args.mode == 'roi_prior'

    split = load_split()
    tr = load_set(split['train'])
    ybase, xbase, pos, marg, prior = slice_tables(tr, prior_mode)

    # validation: the real two-stage inputs (coarse = model 14 integer outputs), fixed for the whole run
    va = load_set(split['val'])
    vcases = []
    for c, (a, b), sp in zip(va['cases'], va['spans'], va['spacing']):
        q0 = np.load(RESULTS / 'quant' / 'z3_r128' / 'outputs' / f'{c}.npy')
        m0 = coarse_mask(q0)
        win = roi_window(m0, sp[2])
        gt = va['lab'][a:b].astype(bool)
        x = fine_inputs(va['img'][a:b], m0, win, args.mode, sp[2]).transpose(0, 3, 1, 2).copy()
        vcases.append(dict(case=c, win=win, x=x, gt=gt, nz=b - a, single=dice3d(m0, gt)))
    single = float(np.mean([v['single'] for v in vcases]))
    print(f'train {len(tr["img"])} slices: {len(pos)} with spleen + {len(marg)} margin slices in range; '
          f'val single-stage 3D Dice {single:.4f}; mode {args.mode}', flush=True)

    xg = torch.from_numpy(tr['img']).to(device)
    yg = torch.from_numpy(tr['lab']).to(device)
    pg = torch.from_numpy(tr['prev']).to(device)
    ng = torch.from_numpy(tr['next']).to(device)
    prg = torch.from_numpy(prior).to(device) if prior_mode else None
    ybg, xbg = torch.from_numpy(ybase).to(device), torch.from_numpy(xbase).to(device)
    posg, margg = torch.from_numpy(pos).to(device), torch.from_numpy(marg).to(device)

    model = get_arch('unet').to(device)
    assert sum(p.numel() for p in model.parameters()) == 34201
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))
    npos = int(round(args.batch * POS_FRAC))
    log, best, t0, run = [], -1.0, time.time(), 0.0
    b = args.batch
    u = lambda lo, hi: torch.rand(b, device=device, generator=gen) * (hi - lo) + lo
    model.train()
    for it in range(1, args.iters + 1):
        idx = torch.cat([posg[torch.randint(0, len(posg), (npos,), device=device, generator=gen)],
                         margg[torch.randint(0, len(margg), (b - npos,), device=device, generator=gen)]])
        jy = torch.randint(-JITTER, JITTER + 1, (b,), device=device, generator=gen)
        jx = torch.randint(-JITTER, JITTER + 1, (b,), device=device, generator=gen)
        y0 = (ybg[idx] + jy).clamp(0, 512 - CROP).float()
        x0 = (xbg[idx] + jx).clamp(0, 512 - CROP).float()
        cur = xg[idx].float()
        if prior_mode:
            nb = torch.floor((xg[pg[idx]].float() + xg[ng[idx]].float() + 1) / 2)
            src = torch.stack([cur / 255, prg[idx].float(), nb / 255, yg[idx].float()], 1)
        else:
            src = torch.stack([xg[pg[idx]].float() / 255, cur / 255, xg[ng[idx]].float() / 255, yg[idx].float()], 1)
        xb, mb = render(src, y0, x0, u(-10, 10) * math.pi / 180, u(0.9, 1.1), u(0.9, 1.1), u(-0.04, 0.04), prior_mode)
        loss = loss_fn(model(xb), mb)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            model.eval()
            rows = []
            for v in vcases:
                lg = predict_case(model, v['x'], device)
                rows.append(dict(case=v['case'], dice=dice3d(combine(lg, v['win'], v['nz'], 0.0), v['gt']), single=v['single']))
            model.train()
            d = float(np.mean([r['dice'] for r in rows]))
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, dice=d, single=single, per_case=rows))
            tag = ''
            if d > best:
                best = d
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'res': 128, 'input': args.mode},
                           out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} val two-stage 3D Dice {d:.4f} (single {single:.4f}) "
                  f"min {min(r['dice'] for r in rows):.3f} ({time.time() - t0:.0f}s){tag}", flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['dice'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps({k: v for k, v in best_row.items() if k != 'per_case'}, indent=1))
    for r in best_row['per_case']:
        print(r)


if __name__ == '__main__':
    main()
