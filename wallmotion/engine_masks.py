r"""Per-frame LV masks + beats for every HMC-QU A4C clip, exactly as the board's echo_ef mode 2 makes them:
verify_board_echo_kf.host_reference (echo_core.dll = tcp_image_rx/src C) on the model 16 integer logits,
recommended "keyframe" schedule and "full" (every frame). No labels used.
Writes results/cache/masks_<sched>.npy (bool [frames, 112, 112]) and results/cache/beats_<sched>.json.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe engine_masks.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, '..', 'tcp_image', 'tcp_image_ui')
sys.path.insert(0, UI)
from verify_board_echo_kf import host_reference  # noqa: E402
import echo_ws  # noqa: E402
import jobs  # noqa: E402
import numpy as np  # noqa: E402

C = os.path.join(HERE, 'results', 'cache')
idx = json.load(open(os.path.join(C, 'index.json')))
F = np.load(os.path.join(C, 'frames.npy'), mmap_mode='r')
Q = np.load(os.path.join(C, 'q.npy'), mmap_mode='r')
conf = echo_ws.load_config()
for sched in ('keyframe', 'full'):
    kf = jobs.load_kf_config(sched)
    masks = np.zeros(F.shape, bool)
    out = {}
    for name, e in idx.items():
        s = slice(e['start'], e['start'] + e['n'])
        h = host_reference(np.ascontiguousarray(F[s]), e['fps'], np.ascontiguousarray(Q[s]), kf, conf)
        masks[s] = np.asarray(h['masks'], bool)
        z = h['z']
        out[name] = dict(key=h['key'], vols=list(h['vols']), ef=h['ef'], ef_raw=z['ef'], hr=z['hr'],
                         fallback=int(z['fallback']), beats=[[int(b[0]), int(b[1])] for b in z['beats']],
                         light=h['light'])
    np.save(os.path.join(C, f'masks_{sched}.npy'), masks)
    json.dump(out, open(os.path.join(C, f'beats_{sched}.json'), 'w'), indent=0)
    nb = [len(v['beats']) for v in out.values()]
    print(sched, 'clips', len(out), 'beats per clip', np.bincount(nb), 'fallback', sum(v['fallback'] for v in out.values()),
          'EF median', np.nanmedian([v['ef'] if v['ef'] is not None else np.nan for v in out.values()]))
