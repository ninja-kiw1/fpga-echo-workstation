"""③ run a segmentation model over whole videos -> per-frame features for beat-to-beat EF and quality control.

  --ckpt results/train/<tag>/best.pth   FP32 on the GPU (development: TRAIN subset / VAL)
  --quant results/quant/<tag>           integer model (= the board): on the GPU with int8_gpu.IntegerModel, after
                                        checking it equals the CPU reference (val_outputs.npy, all VAL ED / ES frames)
Every frame of every video of --split (val / test / trainsub) goes through the board input chain (pad to 128, crop the
logits back to 112). Per frame: raw mask area, number of 4-connected components, cleaned area, single-plane volume
(volume.volume_pca on the cleaned mask), uncertainty = fraction of |logit| < 1 (FP32 units) inside the 3-px dilated
mask, whether the mask touches the 112 frame border. Masks are kept bit-packed.
Writes results/frames/<name>_<split>.npz (+ per-video index).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe -I infer_videos.py --ckpt results/train/t3/best.pth --split trainsub
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'lwnet'))

import numpy as np          # noqa: E402
import torch                # noqa: E402
from scipy import ndimage   # noqa: E402

from common import PAD, RESULTS, load_videos   # noqa: E402
from frame_ops import features, video_inputs   # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ckpt')
    ap.add_argument('--quant')
    ap.add_argument('--split', required=True)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--save-q', action='store_true', help='also keep the int8 logits (large: ~12.5 kB per frame)')
    args = ap.parse_args()
    frames, idx = load_videos(args.split)
    names = sorted(idx)[:args.limit or None]
    if args.ckpt:
        from models.get_model import get_arch
        ck = torch.load(ROOT / args.ckpt, weights_only=True, map_location='cpu')
        mode, name = ck['input'], Path(args.ckpt).parent.name + '_fp32'
        device = torch.device('cuda')
        model = get_arch('unet').to(device).eval()
        model.load_state_dict(ck['model_state_dict'])

        def run(x):
            with torch.no_grad():
                out = [model(torch.from_numpy(x[i:i + 256]).to(device).float().div(255))[:, 0].float().cpu().numpy()
                       for i in range(0, len(x), 256)]
            return np.concatenate(out)[:, PAD:PAD + 112, PAD:PAD + 112]
    else:
        from int8_gpu import IntegerModel
        from common import load_frames
        from train_echonet import to_input
        qd = ROOT / args.quant
        plan = json.loads((qd / 'quant_plan.json').read_text(encoding='utf-8'))
        with np.load(qd / 'parameters.npz', allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        summ = json.loads((qd / 'summary.json').read_text(encoding='utf-8'))
        mode, scale, name = summ['input'], plan[-1]['scale'], qd.name + '_int'
        im = IntegerModel(plan, arrays, 'cuda')

        def run_q(x):
            out = [im(x[i:i + 128])[:, 0, PAD:PAD + 112, PAD:PAD + 112].to(torch.int8).cpu().numpy()
                   for i in range(0, len(x), 128)]
            return np.concatenate(out)

        xva, _, _ = load_frames('val')
        same = np.array_equal(run_q(to_input(np.asarray(xva), mode)), np.load(qd / 'val_outputs.npy'))
        print('GPU integer model == CPU reference on all VAL ED/ES frames:', same, flush=True)
        if not same:
            sys.exit('GPU integer path differs from the CPU reference')

        def run(x):
            q = run_q(x)
            # integer decision q >= 1 <=> logit >= scale / 2 ; expressed in FP32-like units for the features
            return (q.astype(np.float32) - 0.5) * scale, q
    feats, packed, index, t0 = [], [], {}, time.time()
    qs = []
    off = 0
    for k, v in enumerate(names):
        e = idx[v]
        fr = np.asarray(frames[e['start']:e['start'] + e['n']])
        out = run(video_inputs(fr, mode, e['fps']))
        if isinstance(out, tuple):
            out, q = out
            if args.save_q:
                qs.append(q)
        for lg in out:
            mc, f = features(lg)
            feats.append(f)
            packed.append(np.packbits(mc))
        index[v] = dict(start=off, n=e['n'], fps=e['fps'], ef=e['ef'], traced=e['traced'])
        off += e['n']
        if (k + 1) % 100 == 0:
            print(f'{k + 1}/{len(names)} videos ({time.time() - t0:.0f}s)', flush=True)
    out_dir = RESULTS / 'frames'
    out_dir.mkdir(parents=True, exist_ok=True)
    f = np.array(feats, dtype=np.float64)
    np.savez(out_dir / f'{name}_{args.split}.npz', area=f[:, 0], ncomp=f[:, 1], area_clean=f[:, 2], vol=f[:, 3],
             unc=f[:, 4], border=f[:, 5].astype(bool), masks=np.stack(packed))
    if qs:
        np.save(out_dir / f'{name}_{args.split}_q.npy', np.concatenate(qs))
    (out_dir / f'{name}_{args.split}.json').write_text(json.dumps(dict(mode=mode, videos=index)), encoding='utf-8')
    print(f'{name} {args.split}: {len(names)} videos, {off} frames ({time.time() - t0:.0f}s)')


if __name__ == '__main__':
    main()
