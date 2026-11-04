"""echo_num.c (moved out of job_echo.c on 10-24) == the Python the v5.20 board was verified against, bit for bit.

EchoNet VAL, every video: en_volume_pca on each cached cleaned mask vs the cached Python volume (t3_int_val.npz 'vol',
volume.volume_pca), and en_analyze vs echonet/beats.analyze (full mode, beats_config_t3_int.json): EF, beat frames,
beat volumes / EFs, HR, fallback, all compared with ==. Also en_clean112 on the raw (q >= 1) masks of the first 50
videos would need the logits - covered instead by the board regression after deployment.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I parity_echo_num.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'echonet'))
import echo_core  # noqa: E402
from beats import analyze  # noqa: E402

E = HERE.parent / 'echonet'


def main():
    cfg = json.loads((E / 'results' / 'beats_config_t3_int.json').read_text(encoding='utf-8'))['cfg']
    z = np.load(E / 'results' / 'frames' / 't3_int_val.npz')
    vol_py, packed = z['vol'], z['masks']
    idx = json.loads((E / 'results' / 'frames' / 't3_int_val.json').read_text(encoding='utf-8'))['videos']
    vol_diff = frames = an_diff = 0
    for v, e in idx.items():
        s, n = e['start'], e['n']
        m = np.unpackbits(packed[s:s + n], axis=1)[:, :112 * 112].reshape(-1, 112, 112)
        vc = np.array([echo_core.volume_pca(x) for x in m])
        vol_diff += int(np.sum(vc != vol_py[s:s + n]))
        frames += n
        a = analyze(vol_py[s:s + n], e['fps'], 1, cfg)
        b = echo_core.analyze(vol_py[s:s + n], e['fps'], 1, cfg)
        same = (a['ef'] == b['ef'] or (np.isnan(a['ef']) and np.isnan(b['ef']))) and a['fallback'] == b['fallback'] \
            and len(a['beats']) == len(b['beats']) and (a['hr'] == b['hr']) and (a.get('ef_sd') == b['ef_sd']) \
            and all((x['ed'], x['es'], x['v_ed'], x['v_es'], x['ef']) == y for x, y in zip(a['beats'], b['beats']))
        an_diff += not same
    res = dict(videos=len(idx), frames=frames, volume_mismatch_frames=vol_diff, analyze_mismatch_videos=an_diff)
    (HERE / 'results' / 'parity_echo_num.json').write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(res)


if __name__ == '__main__':
    main()
