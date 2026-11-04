"""③ EchoNet-Dynamic -> caches (official split kept: TRAIN / VAL / TEST).

frames  (always): every video with exactly two traced frames of >= 21 rows (10,024 of 10,030 have two traced frames;
        frames with incomplete tracings are dropped). Per video the ED (larger traced area) and ES frame:
          <split>_img112.npy  [2V, 3, 112, 112] uint8 grey ((B + G + R + 1) // 3): frames t - d, t, t + d,
                              d = temporal_offset(fps) (about 40 ms, at least 1 frame, clamped at the video ends)
          <split>_lab112.npy  [2V, 112, 112] uint8 LV mask (official polygon)
          <split>_meta.json   video, frame, phase, EF / EDV / ESV / FPS from FileList, tracing disk volume,
                              EF from the two tracings (method of disks) = what a perfect segmentation could reach
videos  (--videos val test ...): whole videos for beat-to-beat EF, concatenated:
          <split>_videos.npy  [T, 112, 112] uint8, <split>_videos.json  {video: start, n, fps, ef, edv, esv, traced frames}
        --train-videos K adds the first K TRAIN videos (sorted by name) for calibration on the training set.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I prep_echonet.py --videos val test --train-videos 1500
"""
import argparse
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # run with python -I (downloaded data)
import numpy as np                                            # noqa: E402

from common import CACHE, DATA, disk_volume, file_list, read_video, temporal_offset, trace_mask, tracings   # noqa: E402

TR = None


def _init():
    global TR
    TR = tracings()


def two_frames(row):
    name = row['name']
    t = TR.get(name, {})
    frames = [k for k in t if len(t[k]) >= 21]
    if len(t) != 2 or len(frames) != 2:
        return name, None
    v, fps = read_video(DATA / 'Videos' / name)
    if max(frames) >= len(v):
        return name, None
    m = {k: trace_mask(t[k]) for k in frames}
    ed, es = sorted(frames, key=lambda k: -int(m[k].sum()))
    vol = {k: disk_volume(t[k]) for k in frames}
    d = temporal_offset(float(row['FPS']))
    tri = lambda k: v[[max(k - d, 0), k, min(k + d, len(v) - 1)]]
    return name, dict(img=np.stack([tri(ed), tri(es)]), lab=np.stack([m[ed], m[es]]).astype(np.uint8),
                      ed=ed, es=es, area=[int(m[ed].sum()), int(m[es].sum())], vol=[vol[ed], vol[es]],
                      nframes=len(v), fps_video=fps)


def whole(row):
    v, fps = read_video(DATA / 'Videos' / row['name'])
    return row['name'], v, fps


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--videos', nargs='*', default=[], choices=('val', 'test'))
    ap.add_argument('--train-videos', type=int, default=0)
    ap.add_argument('--skip-frames', action='store_true')
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows = file_list()
    by_split = {s: [r for r in rows if r['Split'] == s.upper()] for s in ('train', 'val', 'test')}
    tr = tracings()

    if not args.skip_frames:
        with Pool(8, initializer=_init) as pool:
            for s, rs in by_split.items():
                imgs, labs, meta, dropped = [], [], [], []
                for r, (name, d) in zip(rs, pool.imap(two_frames, rs, chunksize=8)):
                    if d is None:
                        dropped.append(name)
                        continue
                    imgs.append(d['img'])
                    labs.append(d['lab'])
                    ef_disk = 100 * (d['vol'][0] - d['vol'][1]) / d['vol'][0]
                    for ph, k in (('ED', 0), ('ES', 1)):
                        meta.append(dict(video=name, phase=ph, frame=d['ed'] if k == 0 else d['es'],
                                         ef=float(r['EF']), edv=float(r['EDV']), esv=float(r['ESV']), fps=float(r['FPS']),
                                         nframes=d['nframes'], area_px=d['area'][k], disk_vol_px=round(d['vol'][k], 1),
                                         tdelta=temporal_offset(float(r['FPS'])),
                                         ef_disk_trace=round(ef_disk, 3)))
                np.save(CACHE / f'{s}_img112.npy', np.concatenate(imgs))
                np.save(CACHE / f'{s}_lab112.npy', np.concatenate(labs))
                (CACHE / f'{s}_meta.json').write_text(json.dumps(dict(dropped=dropped, frames=meta)), encoding='utf-8')
                ef = np.array([m['ef'] for m in meta[::2]])
                efd = np.array([m['ef_disk_trace'] for m in meta[::2]])
                print(f'{s}: {len(meta) // 2} videos ({len(dropped)} dropped), EF from tracings vs FileList: '
                      f'MAE {np.mean(np.abs(efd - ef)):.2f}, r {np.corrcoef(ef, efd)[0, 1]:.4f} ({time.time() - t0:.0f}s)',
                      flush=True)

    jobs = [(s, by_split[s]) for s in args.videos]
    if args.train_videos:
        jobs.append(('trainsub', sorted(by_split['train'], key=lambda r: r['name'])[:args.train_videos]))
    for s, rs in jobs:
        total = sum(int(r['NumberOfFrames']) for r in rs)
        mm = np.lib.format.open_memmap(CACHE / f'{s}_videos.npy', mode='w+', dtype=np.uint8, shape=(total, 112, 112))
        idx, off = {}, 0
        with Pool(8) as pool:
            for r, (name, v, fps) in zip(rs, pool.imap(whole, rs, chunksize=4)):
                n = len(v)
                assert off + n <= total, (name, n)
                mm[off:off + n] = v
                t = tr.get(name, {})
                idx[name] = dict(start=off, n=n, fps=float(r['FPS']), fps_video=fps, ef=float(r['EF']), edv=float(r['EDV']),
                                 esv=float(r['ESV']), traced=sorted(k for k in t if len(t[k]) >= 21))
                off += n
        mm.flush()
        assert off == total, (off, total)
        (CACHE / f'{s}_videos.json').write_text(json.dumps(idx), encoding='utf-8')
        print(f'{s}: {len(idx)} whole videos, {total} frames, {total * 112 * 112 / 2**30:.2f} GB ({time.time() - t0:.0f}s)',
              flush=True)


if __name__ == '__main__':
    main()
