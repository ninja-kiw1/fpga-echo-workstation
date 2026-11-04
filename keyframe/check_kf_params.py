"""Offline end-to-end check of the echo_ef mode-2 request (no board needed):
  1. tcp_image_ui/jobs.echo_kf_params packs echo_kf_config.json ("keyframe" and "full") behind the 128-byte block;
  2. the board's own parser (echo_quality.c eq_parse_ext, in echo_core.dll) reads it back: every schedule / tracking
     field, the flags, a / b and all 60 model numbers must equal the config exactly; byte 2 (mode) must be 2;
  3. with the parsed values, the host reference (kf_run_sequence, eq_clip_features, en_analyze, eq_interval - the
     functions job_echo_kf calls, in its order) on the first 5 VAL videos must give exactly what quality_kf.py got.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I check_kf_params.py
"""
import ctypes
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
UI = PLD / 'tcp_image' / 'tcp_image_ui'
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(UI))
sys.path.insert(0, str(PLD / 'quality'))
import numpy as np  # noqa: E402

import echo_core  # noqa: E402
import echo_ws  # noqa: E402
import jobs  # noqa: E402

lib = echo_core.lib
lib.eq_parse_ext.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(echo_core.KfCfg), ctypes.POINTER(ctypes.c_int),
                             ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double), ctypes.POINTER(echo_core.EqModel)]
lib.eq_parse_ext.restype = ctypes.c_int


def parse(params):
    ext = np.frombuffer(params[128:], np.uint8).copy()
    kc, fl, a, b, md = echo_core.KfCfg(), ctypes.c_int(), ctypes.c_double(), ctypes.c_double(), echo_core.EqModel()
    rc = lib.eq_parse_ext(ext.ctypes.data, len(ext), ctypes.byref(kc), ctypes.byref(fl), ctypes.byref(a), ctypes.byref(b),
                          ctypes.byref(md))
    return rc, kc, fl.value, a.value, b.value, md


def main():
    conf = echo_ws.load_config()
    rep = {}
    for mode in ('keyframe', 'full'):
        cfg = jobs.load_kf_config(mode)
        base = jobs.echo_params(conf, 0.0625, 16, False, 100, 50.0)
        params = jobs.echo_kf_params(base, cfg)
        rc, kc, fl, a, b, md = parse(params)
        p, t, m = cfg['policy'], cfg['track'], cfg['model']
        ck = dict(length=len(params) == 128 + 560, mode_byte=params[2] == 2, base_unchanged=params[:2] + params[3:128] == base[:2] + base[3:],
                  magic=rc == 0, kind=kc.kind == jobs.KF_KIND[p['kind']], n=kc.n == p.get('n', 0), n_max=kc.n_max == p.get('n_max', 0),
                  n_min=kc.n_min == p.get('n_min', 1), win=kc.win == (p['win'] if p.get('win') is not None else -1),
                  res=kc.res == (p.get('res') or 0.0), R=kc.R == t['R'], k=kc.k == t['k'], reg=kc.reg == t['reg'],
                  pad=kc.pad == t['pad'], spacing=kc.spacing == t['spacing'], flags=fl == 1, a=a == cfg['a'], b=b == cfg['b'],
                  model=(list(md.w) == m['w'] and list(md.mu) == m['mu'] and list(md.sd) == m['sd'] and md.t_green == m['t_green']
                         and md.t_red == m['t_red'] and list(md.b) == m['b_sigma'] and list(md.zmu) == m['zmu']
                         and list(md.zsd) == m['zsd'] and md.q80 == m['q80'] and md.q90 == m['q90']))
        # 3. the parsed numbers drive the host reference = quality_kf.py's per-video results
        import quality_kf as qk
        idx, vid, frames, qall = qk.data('val')
        same = []
        for v in sorted(idx)[:5]:
            e, ev = idx[v], vid[v]
            fr = np.ascontiguousarray(frames[ev['start']:ev['start'] + ev['n']])
            q = np.ascontiguousarray(qall[e['start']:e['start'] + e['n']])
            cm, nc = zip(*[echo_core.clean112(x >= 1) for x in q])
            cm, nc = np.ascontiguousarray(np.stack(cm), np.uint8), np.array(nc, np.int32)
            T = len(fr)
            out = np.zeros((T, 112, 112), np.uint8)
            reason = np.zeros(T, np.int32)
            resid = np.zeros(T)
            lib.kf_run_sequence(ctypes.byref(kc), fr.ctypes.data, cm.ctypes.data, T, 112, 112, float(e['fps']),
                                out.ctypes.data, reason.ctypes.data, resid.ctypes.data)
            feats = echo_core.clip_features(fr, q, out.astype(bool), reason != 0, nc)
            vols = [echo_core.volume_pca(x) for x in out]
            z = echo_core.analyze(vols, e['fps'], 1, conf['cfg'])
            ef = a * z['ef'] + b
            o = echo_core.EqInterval()
            f9 = np.ascontiguousarray([feats[k] for k in echo_core.QF], np.float64)
            lib.eq_interval(ctypes.byref(md), ef, (z['ef_sd'] or 0.0) * abs(a), len(z['beats']), int(z['fallback']),
                            f9.ctypes.data, ctypes.byref(o))
            ref = qk.run_video(('val', v))[mode]
            same.append(feats == ref['feat'] and vols == ref['vol'])
        ck['reference_5_videos'] = all(same)
        rep[mode] = ck
        print(mode, 'PASS' if all(ck.values()) else 'FAIL ' + str([k for k, x in ck.items() if not x]))
    ok = all(all(c.values()) for c in rep.values())
    (HERE / 'results' / 'check_kf_params.json').write_text(json.dumps(dict(pass_=ok, checks=rep), indent=1), encoding='utf-8')
    print('KF_PARAMS_PASS' if ok else 'KF_PARAMS_FAIL')


if __name__ == '__main__':
    main()
