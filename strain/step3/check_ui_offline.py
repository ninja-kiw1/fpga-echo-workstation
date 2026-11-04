r"""Part E step 3: offline check of the PC side of the strain job (no board).

1. tcp_image_ui sequence.load_sequence (the window's own NIfTI reader + 512 stretch) -> strainwin.strain_inputs gives
   the same 512 frames (R channel) and the same sx / sy as strain/step2/track_val.load_sequence, on VAL sequences.
2. jobs.strain request packing (params 32 bytes, data T x 512 x 512).
3. strainwin.unpack on a reply built like job_strain.c builds it (from strain_port.py) gives back the port's
   tracked points / line / myocardium; the window renders it (screenshot results/strainwin_offline.png).
Test split: not used.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe check_ui_offline.py
"""
import json
import struct
import sys
import time
import zlib
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
UI = HERE.parent.parent / 'tcp_image' / 'tcp_image_ui'
sys.path.insert(0, str(UI))
sys.path.insert(0, str(HERE.parent / 'step2'))
import jobs  # noqa: E402
import sequence  # noqa: E402
import strainwin  # noqa: E402
import track_val as tv  # noqa: E402
import strain_port as sp  # noqa: E402


def fake_reply(d, T):
    n = len(d['q8'])
    myo = np.packbits(d['myo'].ravel(), bitorder='little').tobytes()
    res = dict(ok=True, job='strain', frames=T, n_points=n, path_len=len(d['path']), clean='pl', edge_points=d['edges'],
               gls_pct=[x * 100 for x in d['g']], es_raw_pct=d['es_raw_pp'], es_cal_pct=d['es_cal_pp'],
               offset_pct=sp.OFFSET_PP, peak_raw_pct=d['peak_pp'], kernel='neon2',
               ms=dict(total=0.0, segment=0.0, line=0.0, track=0.0, track_per_pair=0.0))
    return dict(result=res, blobs=dict(points=d['q8_t'].astype(np.int32).tobytes(),
                                       line=d['path'].astype(np.int32).tobytes(), myo=myo), wall_s=0.0, upload_s=0.0)


def main():
    meta = json.loads((tv.CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    mi = {(m['patient'], m['view'], m['phase']): i for i, m in enumerate(meta)}
    seqs = sorted({(m['patient'], m['view']) for m in meta})[:6]
    out = dict(inputs=[], unpack=[])
    keep = None
    for p, view in seqs:
        path = tv.CAMUS / 'database_nifti' / p / f'{p}_{view}_half_sequence.nii.gz'
        frames, info = sequence.load_sequence(str(path))
        f512, sx, sy = strainwin.strain_inputs(frames, info)
        fr_t, sx_t, sy_t, _ = tv.load_sequence(p, view, 512)
        same = len(f512) == len(fr_t) and all(np.array_equal(np.frombuffer(a, np.uint8).reshape(512, 512), b)
                                              for a, b in zip(f512, fr_t))
        out['inputs'].append(dict(seq=f'{p}_{view}', frames_same=bool(same), sx=sx, sy=sy, sx_tv=sx_t, sy_tv=sy_t,
                                  spacing_same=bool(sx == sx_t and sy == sy_t)))
        head, body = jobs.pack_request(jobs.JOB_STRAIN, 0, jobs.strain_params(sx, sy), b''.join(f512))
        assert len(body) == 32 + len(f512) * 512 * 512 and struct.unpack_from('<2d', body) == (sx, sy)
        assert zlib.crc32(body) & 0xFFFFFFFF == struct.unpack_from('<I', head, 16)[0]
        i = mi[(p, view, 'ED')]
        q = {t: np.load(tv.ECHO / 'results' / 'quant' / t / 'outputs' / f'val_{i:03d}.npy') >= 1
             for t in ('lv_r128', 'epi_r128')}
        d = sp.run(fr_t, q['lv_r128'], q['epi_r128'], sx_t, sy_t)
        u = strainwin.unpack(fake_reply(d, len(fr_t)))
        out['unpack'].append(dict(seq=f'{p}_{view}', points=bool(np.array_equal(u['points'], d['q8_t'] / 256.0)),
                                  line=bool(np.array_equal(u['line'], d['path'])),
                                  myo=bool(np.array_equal(u['myo'], d['myo']))))
        print(out['inputs'][-1], out['unpack'][-1], flush=True)
        if keep is None:
            keep = (frames, info, fake_reply(d, len(fr_t)))
    ok = all(r['frames_same'] and r['spacing_same'] for r in out['inputs']) and \
        all(r['points'] and r['line'] and r['myo'] for r in out['unpack'])
    # window with a simulated result
    import tkinter as tk
    root = tk.Tk()

    class App:
        link, busy = type('Link', (), {'close': lambda self: None})(), False
        var_host, var_port = tk.StringVar(value='0'), tk.StringVar(value='0')

    class Seq:
        pass
    sw = Seq()
    sw.top, sw.app, sw.busy, sw.frames, sw.info = root, App(), False, keep[0], keep[1]
    reply = keep[2]
    strainwin.run_strain = lambda link, frames, info, flags=0: dict(strainwin.unpack(reply), wall_s=0.0, upload_s=0.0)
    win = strainwin.StrainWindow(sw)
    t0 = time.time()
    while time.time() - t0 < 3:
        root.update()
        time.sleep(0.02)
    win.show(len(keep[0]) // 2)
    root.update()
    shot = None
    try:
        from PIL import ImageGrab
        top = win.top
        x, y, w, h = top.winfo_rootx(), top.winfo_rooty(), top.winfo_width(), top.winfo_height()
        shot = str(HERE / 'results' / 'strainwin_offline.png')
        ImageGrab.grab((x, y, x + w, y + h)).save(shot)
    except Exception as e:
        print('screenshot failed', e)
    out['window'] = dict(value=win.var_val.get(), text=win.var_txt.get(), screenshot=shot)
    root.destroy()
    out['verdict'] = 'STRAIN_UI_OFFLINE_PASS' if ok else 'STRAIN_UI_OFFLINE_FAIL'
    (HERE / 'results' / 'check_ui_offline.json').write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding='utf-8')
    print(out['window'], out['verdict'])


if __name__ == '__main__':
    main()
