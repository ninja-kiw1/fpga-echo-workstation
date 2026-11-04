"""F2 (+F3): click-correctable LV model -- same U-Net, the 3 input channels re-encoded.

  channel R  grey echo frame                      (as F1)
  channel G  prior mask, 0 / 255                  (F1's automatic result, the previous
                                                   click step, or the previous frame)
  channel B  signed click map: 128 + 127*pos - 128*neg, pos/neg = max over clicks of a
             cone max(0, 1 - d / R), R = 40 px on the 512 canvas (10 px at 128)

The board still just runs the 30-node table on an "RGB" image; only weights change.

Training at the 128 tier (what the board computes on), all simulation on the GPU:
  prior: empty (20 %), a neighbouring frame's mask (35 %) or the frame's own mask
         (45 %), then random dilation / erosion, a small misregistration warp, spurious
         blobs and holes;
  clicks: 0-6, each placed inside the interior of the larger remaining error region
         of the prior (missed LV -> positive, false LV -> negative), the area within R
         of a click counts as handled; occasional confirmation clicks.
Validation (official 50 validation patients, expert ED / ES frames only), standard
iterative protocol: click at the centre (max distance transform) of the largest
error component of the current output, the output becomes the next prior; Dice
after k = 0..5 clicks at the original resolution, from the F1 result and from empty.

    python train_inter.py --tag inter_r128
"""
import argparse
import json
import math
import time

import numpy as np
import torch
import torch.nn.functional as F
from scipy import ndimage

from common import RESULTS, CACHE, board_upsample, load_cache, seg_metrics
from models.get_model import get_arch

R128 = 10.0                      # click cone radius at 128 (= 40 px on the 512 canvas)
MAX_CLICKS = 6


def click_channel(pos, neg):
    """uint8-exact signed click encoding, returned in [0, 1] like the board's input scale."""
    return torch.clamp(torch.round(128 + 127 * pos - 128 * neg), 0, 255) / 255


def cone(cy, cx, h, w, device):
    yy = torch.arange(h, device=device, dtype=torch.float32).view(1, h, 1)
    xx = torch.arange(w, device=device, dtype=torch.float32).view(1, 1, w)
    d = torch.sqrt((yy - cy.view(-1, 1, 1)) ** 2 + (xx - cx.view(-1, 1, 1)) ** 2)
    return torch.clamp(1 - d / R128, min=0)


def dilate(m, k):
    return F.max_pool2d(m, k, 1, k // 2)


def erode(m, k):
    return 1 - F.max_pool2d(1 - m, k, 1, k // 2)


def warp(x, gen, rot, scale, shift, mode='bilinear'):
    b, dev = x.shape[0], x.device
    u = lambda lo, hi: torch.rand(b, device=dev, generator=gen) * (hi - lo) + lo
    a = u(-rot, rot) * math.pi / 180
    s = u(1 - scale, 1 + scale)
    tx, ty = u(-shift, shift), u(-shift, shift)
    cos, sin = torch.cos(a) / s, torch.sin(a) / s
    theta = torch.stack([torch.stack([cos, -sin, tx], 1), torch.stack([sin, cos, ty], 1)], 1)
    grid = F.affine_grid(theta, x.shape, align_corners=False)
    return F.grid_sample(x, grid, mode=mode, padding_mode='zeros', align_corners=False)


def sample_points(region, gen):
    """One random point per sample inside `region` (B,1,H,W), preferring its interior."""
    b, _, h, w = region.shape
    inner = erode(region, 3)
    wgt = torch.where(inner.flatten(1).sum(1, keepdim=True) > 0, inner.flatten(1), region.flatten(1))
    valid = wgt.sum(1) > 0
    idx = torch.multinomial(wgt + 1e-12 * (~valid).float().unsqueeze(1), 1, generator=gen).squeeze(1)
    cy = (idx // w).float() + torch.rand(b, device=region.device, generator=gen) - 0.5
    cx = (idx % w).float() + torch.rand(b, device=region.device, generator=gen) - 0.5
    return cy, cx, valid


def make_batch(img, lv, nbr_of, idx, gen, f1prior=None, mix='v2'):
    """Simulated (input, target) for frames idx. img/lv: uint8 (N,128,128) on GPU.

    mix v1: prior = own mask 45 % / neighbour 35 % / empty 20 %, all heavily perturbed.
    mix v2: prior = F1's real prediction 40 % / neighbour 20 % / own mask 25 % / empty 15 %;
            the F1 and own-mask priors get only mild perturbation, so the model learns
            to keep a mostly-right prior and correct it where the clicks are.
    """
    dev = img.device
    b = idx.numel()
    u = lambda: torch.rand(b, device=dev, generator=gen)
    x = img[idx].float().unsqueeze(1) / 255
    gt = lv[idx].float().unsqueeze(1)
    # prior source
    kind = u()
    nbr = nbr_of(idx, gen)
    if mix == 'v1':
        p_nbr, p_f1, p_empty = 0.35, 0.0, 0.2
    else:
        p_nbr, p_f1, p_empty = 0.2, 0.4, 0.15
    prior = torch.where((kind < p_nbr).view(-1, 1, 1, 1), lv[nbr].float().unsqueeze(1), gt)
    if f1prior is not None and p_f1 > 0:
        use_f1 = ((kind >= p_nbr) & (kind < p_nbr + p_f1)).view(-1, 1, 1, 1)
        prior = torch.where(use_f1, f1prior[idx].float().unsqueeze(1), prior)
    mild = (kind >= p_nbr) & (kind < 1 - p_empty) & (u() < (0.0 if mix == 'v1' else 0.6))
    # joint geometric augmentation of frame, target and prior
    both = warp(torch.cat([x, gt, prior], 1), gen, 15, 0.15, 0.08)
    x, gt, prior = both[:, :1], (both[:, 1:2] >= 0.5).float(), (both[:, 2:] >= 0.5).float()
    # prior perturbation: morphology, misregistration, blobs, holes
    ks = torch.randint(0, 9, (b,), device=dev, generator=gen)      # 0..2 none, 3..5 dilate, 6..8 erode
    ks = torch.where(mild, torch.zeros_like(ks), ks)
    unperturbed = prior.clone()
    for j, k in enumerate((3, 5, 7)):
        prior = torch.where((ks == 3 + j).view(-1, 1, 1, 1), dilate(prior, k), prior)
        prior = torch.where((ks == 6 + j).view(-1, 1, 1, 1), erode(prior, k), prior)
    prior = (warp(prior, gen, 6, 0.06, 0.05) >= 0.5).float()
    prior = torch.where(mild.view(-1, 1, 1, 1), unperturbed, prior)
    yy = torch.rand(b, device=dev, generator=gen) * 128
    xx = torch.rand(b, device=dev, generator=gen) * 128
    rr = torch.rand(b, device=dev, generator=gen) * 10 + 3
    blob = (cone(yy, xx, 128, 128, dev) > 1 - rr.view(-1, 1, 1) / R128).float().unsqueeze(1)
    add = ((u() < 0.2) & ~mild).view(-1, 1, 1, 1)
    prior = torch.where(add, torch.maximum(prior, blob), prior)
    cy, cx, ok = sample_points(prior, gen)
    rr = torch.rand(b, device=dev, generator=gen) * 10 + 5
    hole = (cone(cy, cx, 128, 128, dev) > 1 - rr.view(-1, 1, 1) / R128).float().unsqueeze(1)
    cut = ((u() < 0.2) & ok & ~mild).view(-1, 1, 1, 1)
    prior = torch.where(cut, prior * (1 - hole), prior)
    prior = torch.where((kind >= 1 - p_empty).view(-1, 1, 1, 1), torch.zeros_like(prior), prior)   # empty prior
    # clicks on the remaining error regions
    n = torch.multinomial(torch.tensor([0.15, 0.25, 0.2, 0.15, 0.1, 0.1, 0.05], device=dev).expand(b, -1),
                          1, generator=gen).squeeze(1)
    fn, fp = gt * (1 - prior), prior * (1 - gt)
    pos = torch.zeros(b, 128, 128, device=dev)
    neg = torch.zeros(b, 128, 128, device=dev)
    for j in range(MAX_CLICKS):
        active = n > j
        a_fn, a_fp = fn.flatten(1).sum(1), fp.flatten(1).sum(1)
        want_pos = a_fn >= a_fp
        want_pos = torch.where((u() < 0.15) & (a_fn > 0) & (a_fp > 0), ~want_pos, want_pos)
        none_left = (a_fn + a_fp) == 0
        region = torch.where(want_pos.view(-1, 1, 1, 1), fn, fp)
        confirm = none_left & (u() < 0.5)                              # confirmation click inside the LV
        region = torch.where(confirm.view(-1, 1, 1, 1), gt, region)
        want_pos = want_pos | confirm
        cy, cx, valid = sample_points(region, gen)
        place = active & valid
        c = cone(cy, cx, 128, 128, dev) * place.view(-1, 1, 1).float()
        pos = torch.where(want_pos.view(-1, 1, 1), torch.maximum(pos, c), pos)
        neg = torch.where(~want_pos.view(-1, 1, 1), torch.maximum(neg, c), neg)
        handled = (c > 0).float().unsqueeze(1)
        fn, fp = fn * (1 - handled), fp * (1 - handled)
    x = torch.round(x.clamp(0, 1) * 255) / 255
    inp = torch.cat([x, prior, click_channel(pos, neg).unsqueeze(1)], 1)
    return inp, gt


def loss_fn(logits, target):
    bce = F.binary_cross_entropy_with_logits(logits, target)
    p = torch.sigmoid(logits)
    inter = (p * target).sum(dim=(1, 2, 3))
    dice = (2 * inter + 1) / (p.sum(dim=(1, 2, 3)) + target.sum(dim=(1, 2, 3)) + 1)
    return bce + (1 - dice).mean()


def next_click(pred, gt):
    """Standard protocol: centre of the largest error component -> (y, x, positive?)."""
    err = pred != gt
    if not err.any():
        return None
    lab, n = ndimage.label(err)
    sizes = ndimage.sum(err, lab, range(1, n + 1))
    comp = lab == (int(np.argmax(sizes)) + 1)
    dt = ndimage.distance_transform_edt(np.pad(comp, 1))[1:-1, 1:-1]
    y, x = np.unravel_index(int(np.argmax(dt)), dt.shape)
    return y, x, bool(gt[y, x])


def click_maps(clicks, size=128):
    pos = np.zeros((size, size), np.float32)
    neg = np.zeros((size, size), np.float32)
    yy, xx = np.mgrid[0:size, 0:size]
    for y, x, positive in clicks:
        c = np.clip(1 - np.sqrt((yy - y) ** 2 + (xx - x) ** 2) / R128, 0, None)
        if positive:
            pos = np.maximum(pos, c)
        else:
            neg = np.maximum(neg, c)
    return np.clip(np.round(128 + 127 * pos - 128 * neg), 0, 255).astype(np.uint8)


def click_curve(predict, img128, gt128, prior0, cells, rows, cols, k_max=5):
    """predict(list of uint8 (128,128,3)) -> list of bool (128,128). Returns dice[k][i] and clicks."""
    n = len(img128)
    prior = [p.copy() for p in prior0]
    clicks = [[] for _ in range(n)]
    dice = []
    for k in range(k_max + 1):
        rgb = [np.stack([img128[i], prior[i].astype(np.uint8) * 255, click_maps(clicks[i])], -1) for i in range(n)]
        pred = predict(rgb)
        dice.append([seg_metrics(board_upsample(pred[i], 2), cells[i], rows[i], cols[i])['dice'] for i in range(n)])
        for i in range(n):
            c = next_click(pred[i], gt128[i])
            if c is not None:
                clicks[i].append(c)
            prior[i] = pred[i]
    return dice, clicks


def load_val(f1_tag='lv_r128'):
    x, y, cells, rows, cols, meta = load_cache('val')
    img128 = np.asarray(x)[:, ::4, ::4].copy()
    gt128 = np.asarray(y).reshape(-1, 128, 4, 128, 4).mean(axis=(2, 4)) >= 0.5
    f1 = [np.load(RESULTS / 'quant' / f1_tag / 'outputs' / f'val_{i:03d}.npy') >= 1 for i in range(len(meta))]
    return img128, gt128, f1, cells, rows, cols, meta


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--iters', type=int, default=30000)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--eval-every', type=int, default=1000)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--mix', choices=('v1', 'v2'), default='v2')
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

    def nbr_of(idx, g):
        t = idx - start[idx]
        k = torch.randint(1, 7, idx.shape, device=dev, generator=g) * (torch.randint(0, 2, idx.shape, device=dev, generator=g) * 2 - 1)
        return start[idx] + torch.clamp(t + k, torch.zeros_like(t), length[idx] - 1)

    val = load_val()
    img128, gt128, f1, cells, rows, cols, meta = val
    empty = [np.zeros_like(p) for p in f1]

    model = get_arch('unet').to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 500) * 0.5 * (1 + math.cos(math.pi * min(s, args.iters) / args.iters)))

    @torch.no_grad()
    def predict(rgbs):
        model.eval()
        xb = torch.from_numpy(np.stack(rgbs)).to(dev).permute(0, 3, 1, 2).float() / 255
        out = (model(xb)[:, 0] > 0).cpu().numpy()
        model.train()
        return list(out)

    log, best, t0, run = [], -1.0, time.time(), 0.0
    for it in range(1, args.iters + 1):
        idx = torch.randint(0, len(img), (args.batch,), device=dev, generator=gen)
        inp, gt = make_batch(img, lv, nbr_of, idx, gen, f1prior, args.mix)
        loss = loss_fn(model(inp), gt)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if it > 1 else loss.item()
        if it % args.eval_every == 0 or it == args.iters:
            d_f1, _ = click_curve(predict, img128, gt128, f1, cells, rows, cols)
            d_em, _ = click_curve(predict, img128, gt128, empty, cells, rows, cols)
            c_f1 = [float(np.mean(d)) for d in d_f1]
            c_em = [float(np.mean(d)) for d in d_em]
            score = 0.5 * float(np.mean(c_f1[:4])) + 0.5 * float(np.mean(c_em[1:4]))
            log.append(dict(iter=it, loss=run, seconds=time.time() - t0, from_f1=c_f1, from_empty=c_em, score=score))
            tag = ''
            if score > best:
                best = score
                torch.save({'model_state_dict': model.state_dict(), 'iter': it, 'shift': 2}, out / 'best.pth')
                tag = ' *'
            print(f"[{args.tag}] it {it:6d} loss {run:.4f} from F1 k0..5 " + ' '.join(f'{v:.3f}' for v in c_f1)
                  + " | empty k1..5 " + ' '.join(f'{v:.3f}' for v in c_em[1:]) + f" ({time.time() - t0:.0f}s){tag}",
                  flush=True)
            (out / 'log.json').write_text(json.dumps(log, indent=1), encoding='utf-8')
    best_row = max(log, key=lambda r: r['score'])
    (out / 'summary.json').write_text(json.dumps(dict(args=vars(args), best=best_row, last=log[-1],
                                                      f1_val_dice=float(np.mean([seg_metrics(board_upsample(p, 2), cells[i], rows[i], cols[i])['dice'] for i, p in enumerate(f1)]))),
                                                 indent=1), encoding='utf-8')
    print(json.dumps(best_row, indent=1))


if __name__ == '__main__':
    main()
