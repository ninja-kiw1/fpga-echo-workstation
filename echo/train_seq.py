"""F3: frame-to-frame LV propagation model -- same U-Net, the 3 input channels re-encoded.

  enc prev (model echo_seq):  R frame t | G mask of the previous step (0/255) | B frame of the previous step
  enc none (ablation):        R frame t | G mask of the previous step (0/255) | B 128

Why a separate model: reusing the F2 click model (= enc none, trained to keep a mostly
right prior) is smooth but lags the contraction (validation ED->ES: ES Dice 0.876 vs
F1 0.907, FAC error 17 points; eval_f3.py). The previous frame in B gives the motion cue.

Training at the 128 tier on the official training half sequences (labels between ED
and ES are of unstated provenance -- training only). The previous step is t -/+ d,
d = 1 (60 %) / 2 (25 %) / 3 (15 %), both directions; its mask is the label (45 %, mild
perturbation), F1's real prediction (35 %), a heavily perturbed label (15 %) or empty (5 %).
Optional --rollout p: that fraction of iterations instead starts from a label / F1 mask,
lets the current model propagate 1..kmax-1 steps without gradient and trains on the next
step (exposure to its own accumulated errors); --init fine-tunes from an earlier run.
--expert-end: every target is an expert ED / ES frame -- single steps into them (prior from
t -/+ d) and rollouts of 1..kmax steps ending on them (forward to ES, backward to ED). Reason:
the in-between labels fall almost linearly in area from ED to ES while F1 sees the
flat-then-falling shape of real contraction, so they look interpolated.
Validation = seqeval.run_both (forward ED->ES from the F1 board result, backward ES->ED),
score = 0.5 * (forward ES Dice + backward ED Dice), expert ED / ES frames only.

    python train_seq.py --tag seq_prev --enc prev
"""
import argparse
import json
import math
import time

import numpy as np
import torch

from common import CACHE, RESULTS
from models.get_model import get_arch
from seqeval import load_val_seq, run_both
from train_inter import dilate, erode, loss_fn, warp


def make_seq_batch(img, lv, f1prior, start, length, idx, gen, enc, inward=False):
    dev = img.device
    b = idx.numel()
    u = lambda: torch.rand(b, device=dev, generator=gen)
    t = idx - start[idx]
    r = u()
    d = torch.where(r < 0.6, 1, torch.where(r < 0.85, 2, 3))
    sign = torch.where(u() < 0.5, 1, -1)
    if inward:                      # expert targets: the prior comes from inside the sequence
        sign = torch.where(t == 0, -1, 1)
    nb = start[idx] + torch.clamp(t - sign * d, torch.zeros_like(t), length[idx] - 1)
    x = img[idx].float().unsqueeze(1) / 255
    xp = img[nb].float().unsqueeze(1) / 255
    gt = lv[idx].float().unsqueeze(1)
    kind = u()
    prior = torch.where((kind < 0.45).view(-1, 1, 1, 1), lv[nb].float().unsqueeze(1),
                        f1prior[nb].float().unsqueeze(1))
    heavy = (kind >= 0.80) & (kind < 0.95)
    prior = torch.where(heavy.view(-1, 1, 1, 1), lv[nb].float().unsqueeze(1), prior)
    both = warp(torch.cat([x, xp, gt, prior], 1), gen, 15, 0.15, 0.08)
    x, xp = both[:, :1], both[:, 1:2]
    gt, prior = (both[:, 2:3] >= 0.5).float(), (both[:, 3:] >= 0.5).float()
    # mild (label / F1 priors): 50 % a 3x3 dilate or erode, 50 % a tiny misregistration;
    # heavy: 3 / 5 / 7 dilate or erode, then a larger misregistration
    k_size = torch.where(heavy, torch.randint(0, 3, (b,), device=dev, generator=gen),
                         torch.where(u() < 0.5, 0, -1))              # index into (3, 5, 7); -1 = none
    grow = u() < 0.5
    for j, k in enumerate((3, 5, 7)):
        sel = k_size == j
        prior = torch.where((sel & grow).view(-1, 1, 1, 1), dilate(prior, k), prior)
        prior = torch.where((sel & ~grow).view(-1, 1, 1, 1), erode(prior, k), prior)
    small = (warp(prior, gen, 3, 0.03, 0.02) >= 0.5).float()
    large = (warp(prior, gen, 8, 0.08, 0.06) >= 0.5).float()
    prior = torch.where(heavy.view(-1, 1, 1, 1), large, torch.where((u() < 0.5).view(-1, 1, 1, 1), small, prior))
    prior = torch.where((kind >= 0.95).view(-1, 1, 1, 1), torch.zeros_like(prior), prior)
    x = torch.round(x.clamp(0, 1) * 255) / 255
    if enc == 'prev':
        third = torch.round(xp.clamp(0, 1) * 255) / 255
    else:
        third = torch.full_like(x, 128 / 255)
    return torch.cat([x, prior, third], 1), gt


def _theta(b, dev, gen, rot, scale, shift):
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    a = u(-rot, rot) * math.pi / 180
    s = u(1 - scale, 1 + scale)
    tx, ty = u(-shift, shift), u(-shift, shift)
    cos, sin = torch.cos(a) / s, torch.sin(a) / s
    return torch.stack([torch.stack([cos, -sin, tx], 1), torch.stack([sin, cos, ty], 1)], 1)


def _apply(x, theta):
    grid = torch.nn.functional.affine_grid(theta, x.shape, align_corners=False)
    return torch.nn.functional.grid_sample(x, grid, mode='bilinear', padding_mode='zeros', align_corners=False)


def expert_rollout_batch(model, img, lv, f1prior, start, length, seq_idx, gen, enc, kmax):
    """Rollouts that END on an expert frame: forward chains ending at ES, backward chains
    ending at ED (the in-between labels look interpolated, so they are never targets here).
    The chain starts from F1's prediction (or the interpolated label, as an input only)."""
    dev = img.device
    b = seq_idx.numel()
    s0, ln = start[seq_idx], length[seq_idx]
    to_es = torch.rand(b, device=dev, generator=gen) < 0.5
    d = torch.where(to_es, 1, -1)                                   # chain direction
    end = torch.where(to_es, s0 + ln - 1, s0)
    k = torch.randint(1, kmax + 1, (1,), device=dev, generator=gen).item()
    theta = _theta(b, dev, gen, 15, 0.15, 0.08)
    lo, hi = s0, s0 + ln - 1
    frames = [torch.minimum(torch.maximum(end - d * (k - j), lo), hi) for j in range(k + 1)]
    q = lambda v: torch.round(_apply(v, theta).clamp(0, 1) * 255) / 255
    xs = [q(img[f].float().unsqueeze(1) / 255) for f in frames]
    use_f1 = (torch.rand(b, device=dev, generator=gen) < 0.7).view(-1, 1, 1, 1)
    prior = torch.where(use_f1, f1prior[frames[0]].float().unsqueeze(1), lv[frames[0]].float().unsqueeze(1))
    prior = (_apply(prior, theta) >= 0.5).float()
    third = lambda j: xs[j - 1] if enc == 'prev' else torch.full_like(xs[j], 128 / 255)
    was = model.training
    model.eval()
    with torch.no_grad():
        for j in range(1, k):
            prior = (model(torch.cat([xs[j], prior, third(j)], 1)) > 0).float()
    model.train(was)
    gt = (_apply(lv[frames[k]].float().unsqueeze(1), theta) >= 0.5).float()
    return torch.cat([xs[k], prior, third(k)], 1), gt


def make_rollout_batch(model, img, lv, f1prior, start, length, idx, gen, enc, kmax):
    """Exposure to the model's own errors: start from a label / F1 mask at frame t0, let the
    current model propagate K-1 steps (no gradient), train on step K. One warp per sample
    for the whole path, so the motion between frames is real."""
    dev = img.device
    b = idx.numel()
    t = idx - start[idx]
    sign = torch.where(torch.rand(b, device=dev, generator=gen) < 0.5, 1, -1)
    k = torch.randint(2, kmax + 1, (1,), device=dev, generator=gen).item()
    theta = _theta(b, dev, gen, 15, 0.15, 0.08)
    frames = [start[idx] + torch.clamp(t + sign * j, torch.zeros_like(t), length[idx] - 1) for j in range(k + 1)]
    q = lambda v: torch.round(_apply(v, theta).clamp(0, 1) * 255) / 255
    xs = [q(img[f].float().unsqueeze(1) / 255) for f in frames]
    use_f1 = (torch.rand(b, device=dev, generator=gen) < 0.5).view(-1, 1, 1, 1)
    prior = torch.where(use_f1, f1prior[frames[0]].float().unsqueeze(1), lv[frames[0]].float().unsqueeze(1))
    prior = (_apply(prior, theta) >= 0.5).float()
    third = lambda j: xs[j - 1] if enc == 'prev' else torch.full_like(xs[j], 128 / 255)
    was = model.training
    model.eval()
    with torch.no_grad():
        for j in range(1, k):
            prior = (model(torch.cat([xs[j], prior, third(j)], 1)) > 0).float()
    model.train(was)
    gt = (_apply(lv[frames[k]].float().unsqueeze(1), theta) >= 0.5).float()
    return torch.cat([xs[k], prior, third(k)], 1), gt


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--iters', type=int, default=30000)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--enc', choices=('prev', 'none'), required=True)
    ap.add_argument('--rollout', type=float, default=0.0, help='fraction of iterations trained on own rollouts')
    ap.add_argument('--kmax', type=int, default=8)
    ap.add_argument('--init', default=None, help='start from results/train/<tag>/best.pth')
    ap.add_argument('--expert-end', action='store_true',
                    help='all targets are expert ED / ES frames (single steps into them + rollouts ending on them)')
    ap.add_argument('--tag', required=True)
    args = ap.parse_args()
    out = RESULTS / 'train' / args.tag
    out.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    dev = torch.device('cuda')
    gen = torch.Generator(device=dev).manual_seed(args.seed)

    img = torch.from_numpy(np.load(CACHE / 'seq_train_img128.npy')).to(dev)
    lv = torch.from_numpy(np.load(CACHE / 'seq_train_lv128.npy')).to(dev)
    f1prior = torch.from_numpy(np.load(CACHE / 'seq_train_f1prior128.npy')).to(dev)
    index = json.loads((CACHE / 'seq_train_index.json').read_text(encoding='utf-8'))
    start = torch.zeros(len(img), dtype=torch.long, device=dev)
    length = torch.zeros(len(img), dtype=torch.long, device=dev)
    for s in index:
        start[s['start']:s['start'] + s['length']] = s['start']
        length[s['start']:s['start'] + s['length']] = s['length']
    V = load_val_seq()
    seq_starts = torch.tensor([s['start'] for s in index], device=dev)
    expert = torch.cat([seq_starts, seq_starts + torch.tensor([s['length'] - 1 for s in index], device=dev)])

    model = get_arch('unet').to(dev)
    if args.init:
        model.load_state_dict(torch.load(RESULTS / 'train' / args.init / 'best.pth', weights_only=True)['model_state_dict'])
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))

    @torch.no_grad()
    def predict(rgb):
        model.eval()
        xb = torch.from_numpy(rgb).to(dev).permute(0, 3, 1, 2).float() / 255
        m = (model(xb)[:, 0] > 0).cpu().numpy()
        model.train()
        return m

    log, best, t0, run = [], -1.0, time.time(), 0.0
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(img), (args.batch,), device=dev, generator=gen)
        if args.expert_end:
            if torch.rand(1, device=dev, generator=gen).item() < args.rollout:
                sidx = seq_starts[torch.randint(0, len(seq_starts), (args.batch,), device=dev, generator=gen)]
                inp, gt = expert_rollout_batch(model, img, lv, f1prior, start, length, sidx, gen, args.enc, args.kmax)
            else:
                eidx = expert[torch.randint(0, len(expert), (args.batch,), device=dev, generator=gen)]
                inp, gt = make_seq_batch(img, lv, f1prior, start, length, eidx, gen, args.enc, inward=True)
        elif args.rollout and torch.rand(1, device=dev, generator=gen).item() < args.rollout:
            inp, gt = make_rollout_batch(model, img, lv, f1prior, start, length, idx, gen, args.enc, args.kmax)
        else:
            inp, gt = make_seq_batch(img, lv, f1prior, start, length, idx, gen, args.enc)
        loss = loss_fn(model(inp), gt)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            r, _ = run_both(predict, V, args.enc)
            row = dict(iter=it, loss=run, seconds=time.time() - t0, score=r['score'],
                       fwd={k: r['fwd'][k] for k in ('es_dice', 'fac_abs_err_pct', 'jump_mean', 'keyframe_exact_es')},
                       bwd={k: r['bwd'][k] for k in ('ed_dice', 'fac_abs_err_pct', 'jump_mean', 'keyframe_exact_ed')})
            log.append(row)
            tag = ''
            if r['score'] > best:
                best = r['score']
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'shift': 2, 'enc': args.enc},
                           out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} score {r['score']:.4f} | fwd ES {r['fwd']['es_dice']:.4f} "
                  f"FAC err {r['fwd']['fac_abs_err_pct']:.1f} jump {r['fwd']['jump_mean']:.4f} | bwd ED "
                  f"{r['bwd']['ed_dice']:.4f} FAC err {r['bwd']['fac_abs_err_pct']:.1f} ({time.time() - t0:.0f}s){tag}",
                  flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['score'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1]), indent=1),
                                      encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
