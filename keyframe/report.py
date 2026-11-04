"""Keyframe step 3: working points (EVA2 rule), per-frame budget, Pareto figure.

Working points: among all EchoNet VAL settings, the one with the fewest keyframes whose mean expert Dice (2 traced
frames per video) loses < 0.5 / 1 / 2 % against per-frame inference ('full'). Chosen on VAL (the development split;
TEST untouched), so the VAL numbers of the chosen points are optimistic by selection.
Budget: PL 22.05 ms per keyframe (128 tier, f150, board P50, perf_j), PS tracking per frame estimated from the board
kernel timing of part E (neon2 91.5-95.8 ns per 16 x 16 candidate, 10-17) x contour points (P95) x candidates.

    python -I report.py      -> results/working_points.json, results/keyframe_pareto.png
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / 'results'
PL_MS = 22.05          # 128 tier per inference, board P50 (f150, J1, 10-20 slice)
NS_CAND = 95.8         # neon2 per candidate, R = 4 (board 10-17, worst of R 4/6/8)


def track_ms(points, R, agg_us=1.0):
    return points * ((2 * R + 1) ** 2 * NS_CAND / 1000 + agg_us) / 1000


def main():
    s = json.loads((OUT / 'echonet_summary.json').read_text(encoding='utf-8'))
    ted = json.loads((OUT / 'ted_policies.json').read_text(encoding='utf-8'))['summary']
    full = s['full']['expert_dice']
    rows = {k: v for k, v in s.items() if not k.startswith('_')}
    wp = {}
    for lim in (0.005, 0.01, 0.02):
        ok = [(v['key_frac'], k) for k, v in rows.items() if v['expert_dice'] >= full * (1 - lim)]
        kf, k = min(ok)
        v = rows[k]
        wp[f'loss<{lim * 100:g}%'] = dict(setting=k, key_frac=kf, key_per_s=v['key_per_s'], expert_dice=v['expert_dice'],
                                          dice_loss_pct=100 * (1 - v['expert_dice'] / full), ef_mae=v['ef']['mae'],
                                          agree=v['agree'])
    t_echo = track_ms(106, 3)       # EchoNet 112 grid: P95 106 points, R 3
    t_ted = track_ms(112, 4)        # 256 tier: P95 112 points, R 4
    budget = {}
    for fps in (30, 50, 60):
        b = {}
        for name, kfrac in [('full', 1.0)] + [(k, v['key_frac']) for k, v in wp.items()]:
            pl = fps * kfrac * PL_MS
            b[name] = dict(pl_ms_per_s=pl, pl_duty=pl / 1000, ps_track_ms_per_s=fps * (1 - kfrac) * t_ted,
                           ps_core_duty=fps * (1 - kfrac) * t_ted / 1000, keeps_up=pl < 1000)
        budget[f'{fps}fps'] = b
    res = dict(full=dict(expert_dice=full, ef_mae=s['full']['ef']['mae']), working_points=wp,
               track_ms_est=dict(echonet_112=t_echo, tier256=t_ted), pl_ms=PL_MS, budget=budget,
               today_realtime=s['_today_realtime_subsample'])
    (OUT / 'working_points.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(json.dumps(res, indent=1))

    fig, ax = plt.subplots(1, 3, figsize=(15, 4.3))
    groups = [('hold', 'no tracking (copy last keyframe)', 'tab:gray', 'o'), ('fixed', 'fixed interval + tracking', 'tab:blue', 's'),
              ('adapt', 'adaptive (interval cap + residual / phase)', 'tab:orange', '^')]
    for pre, lab, col, mk in groups:
        e = [(v['key_frac'], v['expert_dice'], v['ef']['mae']) for k, v in rows.items() if k.startswith(pre)]
        ax[0].scatter([x[0] for x in e], [x[1] for x in e], c=col, marker=mk, label=lab, s=28)
        ax[1].scatter([x[0] for x in e], [x[2] for x in e], c=col, marker=mk, s=28)
        t = [(v['key_frac'], v['dice_nonkey'], v['ef_mae']) for k, v in ted.items() if k.startswith(pre)]
        ax[2].scatter([x[0] for x in t], [x[1] for x in t], c=col, marker=mk, s=28)
    for a in ax:
        a.axvline(1.0, c='k', lw=0.5)
        a.set_xlabel('keyframe fraction (PL inferences / frames)')
        a.grid(alpha=0.3)
    ax[0].axhline(full, c='k', ls='--', lw=0.8)
    for lim in (0.005, 0.01, 0.02):
        ax[0].axhline(full * (1 - lim), c='tab:red', ls=':', lw=0.7)
    ax[0].scatter([1], [full], c='k', marker='*', s=90, label='every frame (today)')
    ax[0].set_ylabel('Dice vs expert (VAL traced ED/ES)')
    ax[0].set_title('EchoNet VAL: segmentation')
    ax[0].legend(fontsize=7, loc='lower right')
    ax[1].scatter([1], [s['full']['ef']['mae']], c='k', marker='*', s=90)
    ax[1].set_ylabel('EF MAE (calibrated on TRAIN), %')
    ax[1].set_title('EchoNet VAL: beat-to-beat EF')
    ax[2].set_ylabel('Dice of non-keyframes vs expert')
    ax[2].set_title('TED (expert keyframes): propagation error')
    fig.tight_layout()
    fig.savefig(OUT / 'keyframe_pareto.png', dpi=130)


if __name__ == '__main__':
    main()
