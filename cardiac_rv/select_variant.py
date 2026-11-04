"""RV / RA programs, step 6: pick one variant per program with the rule fixed before the student results were seen.

Rule: among the trained variants, keep those whose CardiacUDA VAL Dice >= (wide-augmentation variant's Dice - 0.01);
pick the highest mean of four cross-dataset numbers for that program: EchoNet VAL ED/ES anatomy pass rate,
CAMUS VAL 4CH ED/ES anatomy pass rate, EchoNet VAL video phase rate, CAMUS VAL half-sequence phase rate
(RV: in phase with the LV area; RA: anti-phase). All four use VAL splits, so the chosen numbers are optimistic.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I select_variant.py
"""
import json
from pathlib import Path

R = Path(__file__).resolve().parent / 'results'
VARIANTS = {'base': ('rv_r128', 'ra_r128', 'cross_check.json'), 'wide': ('rv_w', 'ra_w', 'cross_check_rv_w_ra_w.json'),
            'pl1_50': ('rv_pl1_5', 'ra_pl1_5', 'cross_check_rv_pl1_5_ra_pl1_5.json'),
            'pl1_25': ('rv_pl1_25', 'ra_pl1_25', 'cross_check_rv_pl1_25_ra_pl1_25.json')}


def main():
    rows = {}
    for v, (rv, ra, f) in VARIANTS.items():
        c = json.loads((R / f).read_text(encoding='utf-8'))
        for t, tag, phase in (('rv', rv, 'rv_in_phase'), ('ra', ra, 'ra_anti_phase')):
            parts = [c['echonet_val_ed_es'][f'{t}_ok'], c['camus_val_4ch_ed_es'][f'{t}_ok'],
                     c['echonet_val_videos'][phase], c['camus_val_half_sequences'][phase]]
            rows.setdefault(t, {})[v] = dict(tag=tag, dice=c['cardiacuda_val'][f'dice_{t}'], parts=parts,
                                             score=sum(parts) / 4)
    out = {}
    for t, rr in rows.items():
        floor = rr['wide']['dice'] - 0.01
        ok = {v: r for v, r in rr.items() if r['dice'] >= floor}
        best = max(ok, key=lambda v: ok[v]['score'])
        out[t] = dict(chosen=best, tag=rr[best]['tag'], dice_floor=floor, variants=rr)
        for v, r in rr.items():
            print(f"{t} {v:7s} Dice {r['dice']:.4f}{' ' if r['dice'] >= floor else '!'} score {r['score']:.4f} "
                  f"parts {[round(p, 3) for p in r['parts']]}{'  <- chosen' if v == best else ''}")
    (R / 'selection.json').write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
