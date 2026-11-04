"""Write tcp_image_ui/echo_kf_config.json: everything the PC sends to the board for echo_ef mode 2 (jobs.echo_kf_params),
for the two configurations evaluated by quality_kf.py (board C): "keyframe" (recommended schedule
adapt_nmax6_res1.15_winoff, tracking R 3 / k 9 / reg 5 / spacing 2 / pad 24) and "full" (every frame inferred). Each has the
schedule, the tracking parameters, the EF calibration a, b of that schedule (TRAIN subset), the quality model (CAMUS
TRAIN weights + light thresholds, EchoNet TRAIN-subset standardisation) and the interval model (sigma fitted on the
TRAIN subset, q80 / q90 from VAL). Provenance: source files and their SHA-256.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I make_kf_config.py
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
UI = PLD / 'tcp_image' / 'tcp_image_ui'
TRACK = dict(R=3, k=9, reg=5, spacing=2.0, pad=24)
POLICY = {'keyframe': dict(kind='adaptive', n_max=6, n_min=1, res=1.15, win=None),
          'full': dict(kind='full')}


def main():
    out = dict(made=datetime.now(timezone.utc).isoformat(timespec='seconds'), note=__doc__.split('\n\n')[0], sources={})
    for mode in ('keyframe', 'full'):
        p = HERE / 'results' / 'c' / f'eq_model_{mode}.json'
        m = json.loads(p.read_text(encoding='utf-8'))
        out['sources'][mode] = dict(file=str(p.relative_to(PLD)), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
        out[mode] = dict(policy=POLICY[mode], track=TRACK, a=m['a'], b=m['b'],
                         model={k: m[k] for k in ('w', 'mu', 'sd', 't_green', 't_red', 'b_sigma', 'zmu', 'zsd', 'q80', 'q90')},
                         features=m['features'], sigma_inputs=m['sigma_inputs'])
    (UI / 'echo_kf_config.json').write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    print('wrote', UI / 'echo_kf_config.json')


if __name__ == '__main__':
    main()
