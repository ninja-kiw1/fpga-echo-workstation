"""Figure for the strain note: (a) GLS-like curves ED -> ES of 4 VAL sequences (FINAL config, raw) with the
expert mid-wall reference at ES; (b) tracked (TRAIN offset calibration) vs expert ES value for all 100 VAL sequences.
Reads results/track_val.json; writes results/gls_val.png.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe plot_val.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#6250d6']        # categorical slots 1-4 (reference palette)
INK, INK2, GRID, SURF = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'


def main():
    d = json.loads((HERE / 'results' / 'track_val.json').read_text(encoding='utf-8'))
    final = next(k for k in d['summary'] if k.startswith('final_') and k.endswith('_calibrated'))[:-len('_calibrated')]
    cal = d['summary'][final + '_offset']
    off = cal['offset']
    tier = next(c for c in d['configs'] if c['name'] == final)['tier']
    meta = json.loads((HERE.parent.parent / 'echo' / 'data' / 'cache' / 'val_meta.json').read_text(encoding='utf-8'))
    qual = {(m['patient'], m['view']): m['quality'] for m in meta}
    per = d['per']
    plt.rcParams.update({'font.size': 9, 'axes.edgecolor': INK2, 'axes.labelcolor': INK2, 'xtick.color': INK2,
                         'ytick.color': INK2, 'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2), gridspec_kw=dict(width_ratios=[1.25, 1]))
    # (a) four Good-quality examples spread over the reference range
    good = sorted([r for r in per if qual[(r['patient'], r['view'])] == 'Good'], key=lambda r: r[f'ref_mid_{tier}'])
    picks = [good[int(q * (len(good) - 1))] for q in (0.1, 0.4, 0.7, 0.95)]
    for r, col in zip(picks, SERIES):
        g = np.array(r['configs'][final]['curve']) * 100
        t = np.linspace(0, 1, len(g))
        ax1.plot(t, g, color=col, lw=2, label=f"{r['patient'][-4:]} {r['view']}")
        ax1.plot([1], [r[f'ref_mid_{tier}'] * 100], 'o', ms=8, mfc=SURF, mec=col, mew=2)
        ax1.text(1.03, g[-1], f"{r['patient'][-4:]} {r['view']}", color=INK2, va='center', fontsize=8)
    ax1.axhline(0, color=GRID, lw=1, zorder=0)
    ax1.set_xlim(0, 1.25)
    ax1.set_xticks([0, 0.5, 1])
    ax1.set_xticklabels(['ED', '', 'ES'])
    ax1.set_ylabel('GLS-like strain (%)')
    ax1.set_title('(a) tracked mid-wall line, raw, ED to ES (circle = expert ES)', color=INK, fontsize=9, loc='left')
    ax1.legend(frameon=False, fontsize=8, loc='lower left')
    ax1.grid(axis='y', color=GRID, lw=0.8)
    for s in ('top', 'right'):
        ax1.spines[s].set_visible(False)
    # (b) all VAL sequences
    est = np.array([r['configs'][final]['es'] * 100 + off for r in per])
    ref = np.array([r[f'ref_mid_{tier}'] * 100 for r in per])
    poor = np.array([qual[(r['patient'], r['view'])] == 'Poor' for r in per])
    lo, hi = min(est.min(), ref.min()) - 2, max(est.max(), ref.max()) + 2
    ax2.plot([lo, hi], [lo, hi], color=INK2, lw=1, ls='--', zorder=0)
    ax2.plot(ref[~poor], est[~poor], 'o', ms=6, color=SERIES[0], mec=SURF, mew=1, label='Good / Medium')
    ax2.plot(ref[poor], est[poor], 'o', ms=6, mfc=SURF, mec=SERIES[0], mew=1.5, label='Poor')
    s = cal['vs_ref_mid']
    ax2.text(0.04, 0.96, f"n {s['n']}, bias {s['bias']:+.1f}, MAE {s['mae']:.1f} pp, r {s['r']:.2f}",
             transform=ax2.transAxes, va='top', color=INK2, fontsize=8)
    ax2.set_xlim(lo, hi)
    ax2.set_ylim(lo, hi)
    ax2.set_xlabel('expert mid-wall length change (%)')
    ax2.set_ylabel(f'tracked + TRAIN offset {off:+.1f} pp (%)')
    ax2.set_title('(b) VAL, 100 sequences', color=INK, fontsize=9, loc='left')
    ax2.legend(frameon=False, fontsize=8, loc='lower right')
    ax2.grid(color=GRID, lw=0.8)
    for sp in ('top', 'right'):
        ax2.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(HERE / 'results' / 'gls_val.png', dpi=150)
    print('wrote results/gls_val.png')


if __name__ == '__main__':
    main()
