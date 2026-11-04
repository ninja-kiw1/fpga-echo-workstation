"""③ host UI code == offline code, in the UI environment (srtp-vision: OpenCV, no torch). The int8 logits come from the
board itself (verify_board_echonet.py already proved board == integer model on whole videos).

For the first --videos VAL videos, both modes:
  * echo_ws.read_avi == common.read_video (decoded grey frames);
  * echo_ws.payloads == frame_ops.video_inputs (bytes, analysed frames);
  * per analysed frame: echo_ws.frame_info / volume_pca == frame_ops.features (cleaned mask, components, area,
    uncertainty, border, volume);
  * echo_ws.analyze == beats.analyze (whole result dict) with the workstation config (echonet_config.json),
    and the calibrated EF == eval_ef.py's per-video result.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I verify_ui_code_echonet.py --videos 10
"""
import argparse
import json
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLD = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))

import numpy as np          # noqa: E402

import echo_ws              # noqa: E402
import imagelink            # noqa: E402
import proto                # noqa: E402
from beats import analyze, realtime_step   # noqa: E402
from common import DATA, PAD, RESULTS, load_videos, read_video   # noqa: E402
from frame_ops import features, video_inputs   # noqa: E402
from volume import volume_pca   # noqa: E402

F = proto.FLAG_STATS | proto.FLAG_C_LOGIT


def stream(link, payloads, model):
    out, inflight, k = [None] * len(payloads), deque(), 0
    link.close()                      # fresh connection: analysis pauses between streams can exceed
                                      # the server's 10 s idle timeout
    while k < len(payloads) or inflight:
        while len(inflight) < 2 and k < len(payloads):
            link.send_v6c(payloads[k], 128, kind=proto.V6C_KIND_RGB, model0=model, thr=1, flags=F)
            inflight.append(k)
            k += 1
        i = inflight.popleft()
        _, lg, _ = link.recv_v6c(F, 128)
        out[i] = lg
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--videos', type=int, default=10)
    ap.add_argument('--model', type=int, default=16)
    ap.add_argument('--features', default='t3_int')
    args = ap.parse_args()
    conf = json.loads((PLD / 'tcp_image' / 'tcp_image_ui' / 'echonet_config.json').read_text(encoding='utf-8'))
    scale = next(m for m in proto.MODELS if m['index'] == args.model)['scale']
    echo_ws.SCALE[0] = scale
    ev = {r['video']: r for r in json.loads((RESULTS / 'ef' / f'{args.features}_val.json').read_text(encoding='utf-8'))['per_video']}
    _, idx = load_videos('val')
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    rep, ok = {}, True
    for v in sorted(idx)[:args.videos]:
        e = idx[v]
        fu, _ = echo_ws.read_avi(str(DATA / 'Videos' / v))
        fo, _ = read_video(DATA / 'Videos' / v)
        c = dict(read=bool(np.array_equal(fu, fo)))
        xo_all = video_inputs(fo, 't3', e['fps'])
        for mode in ('realtime', 'full'):
            k = realtime_step(e['fps']) if mode == 'realtime' else 1
            ids, pl = echo_ws.payloads(fu, e['fps'], 't3', k)
            xo = xo_all[::k]
            c[f'{mode}_payloads'] = len(pl) == len(xo) and all(p == np.ascontiguousarray(s.transpose(1, 2, 0)).tobytes()
                                                               for p, s in zip(pl, xo))
            same, vo, vu = True, [], []
            for lg in stream(link, pl, args.model):
                q, raw = echo_ws.mask_from_logit(lg)
                mu, fu_ = echo_ws.frame_info(q, raw)
                mo, f = features((q.astype(np.float32) - 0.5) * scale)
                same &= bool(np.array_equal(mu, mo)) and (fu_['ncomp'], fu_['area'], fu_['unc'], fu_['border']) == (f[1], f[2], f[4], f[5])
                vo.append(f[3])
                vu.append(echo_ws.volume_pca(mu))
            c[f'{mode}_frame_info'] = same
            c[f'{mode}_volumes'] = vo == vu and vo == [volume_pca(m) for m in []] + vo
            ra = analyze(vo, e['fps'], k, conf['cfg'])
            rb = echo_ws.analyze(vu, e['fps'], k, conf['cfg'])
            c[f'{mode}_beats'] = ra == rb
            ef = echo_ws.calibrated(rb['ef'], conf, mode)
            c[f'{mode}_ef_matches_eval'] = abs(ef - ev[v][mode]['ef']) < 1e-9
        rep[v] = c
        ok &= all(c.values())
        print(v, c, flush=True)
    out = dict(videos=len(rep), per_video=rep, pass_=bool(ok))
    (ROOT / 'board_check').mkdir(exist_ok=True)
    (ROOT / 'board_check' / 'ui_code_echonet.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print('ECHONET_UI_CODE_PASS' if ok else 'ECHONET_UI_CODE_FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
