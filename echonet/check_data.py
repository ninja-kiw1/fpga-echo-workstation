"""③ EchoNet-Dynamic: integrity check of the downloaded data (E:\\PLD\\EchoNet-Dynamic).

  * FileList.csv: every row has a video; EF / ESV / EDV consistent; split sizes;
  * every .avi is decoded in full with OpenCV (8 processes): frame count, size, FPS vs FileList,
    whether the three colour channels are equal (grey stored as RGB);
  * VolumeTracings.csv: traced frames per video (expected 2: ED and ES), lines per frame
    (expected 21: long axis + 20 chords), frame index inside the video, traced area of both frames
    (the polygon used by the official code), which of the two is larger, and how the
    area-based EF proxy agrees with the reported EF.
Result: results/data_check.json.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I check_data.py
"""
import csv
import json
import sys
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'EchoNet-Dynamic'


def probe(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return dict(ok=False, error='cannot open')
    n_prop = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    n, shape, grey, mean = 0, None, True, 0.0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if shape is None:
            shape = list(fr.shape)
        if grey and fr.ndim == 3 and not (np.array_equal(fr[..., 0], fr[..., 1]) and np.array_equal(fr[..., 1], fr[..., 2])):
            grey = False
        mean += float(fr.mean())
        n += 1
    cap.release()
    return dict(ok=n > 0, frames=n, frames_prop=n_prop, fps=round(fps, 3), shape=shape, grey=grey,
                mean=round(mean / max(n, 1), 2))


def polygon_area(t):
    """t: rows (x1, y1, x2, y2); row 0 = long axis. Same polygon as echonet.datasets.Echo."""
    x = np.concatenate((t[1:, 0], t[1:, 2][::-1]))
    y = np.concatenate((t[1:, 1], t[1:, 3][::-1]))
    return 0.5 * abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1))))


def main():
    t0 = time.time()
    problems = []
    with open(DATA / 'FileList.csv', newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    names = [r['FileName'] if r['FileName'].endswith('.avi') else r['FileName'] + '.avi' for r in rows]
    videos = {p.name for p in (DATA / 'Videos').glob('*.avi')}
    missing = [n for n in names if n not in videos]
    extra = sorted(videos - set(names))
    if missing:
        problems.append('%d FileList rows without a video, e.g. %s' % (len(missing), missing[:5]))
    split = defaultdict(int)
    ef_bad = []
    for r in rows:
        split[r['Split']] += 1
        ef, esv, edv = float(r['EF']), float(r['ESV']), float(r['EDV'])
        if not (0 < ef < 100) or abs((edv - esv) / edv * 100 - ef) > 0.5:
            ef_bad.append((r['FileName'], ef, esv, edv))

    # tracings
    tr = defaultdict(lambda: defaultdict(list))
    with open(DATA / 'VolumeTracings.csv', newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            tr[r['FileName']][int(r['Frame'])].append([float(r['X1']), float(r['Y1']), float(r['X2']), float(r['Y2'])])

    # decode every video
    paths = [DATA / 'Videos' / n for n in names if n in videos]
    with Pool(8) as pool:
        probes = {}
        for i, (p, res) in enumerate(zip(paths, pool.imap(probe, paths, chunksize=16))):
            probes[p.name] = res
            if (i + 1) % 1000 == 0:
                print('decoded %d/%d (%.0fs)' % (i + 1, len(paths), time.time() - t0), flush=True)

    per = []
    cnt = defaultdict(int)
    for r, n in zip(rows, names):
        pr = probes.get(n, dict(ok=False, error='missing'))
        nf = int(r['NumberOfFrames'])
        frames = sorted(tr.get(n, {}))
        lines = [len(tr[n][k]) for k in frames] if n in tr else []
        d = dict(name=n, split=r['Split'], ef=float(r['EF']), frames_csv=nf, fps_csv=float(r['FPS']),
                 size_csv=[int(r['FrameHeight']), int(r['FrameWidth'])], **{'video_' + k: v for k, v in pr.items()},
                 traced_frames=frames, lines_per_frame=lines)
        if not pr['ok']:
            cnt['decode_failed'] += 1
        else:
            if pr['frames'] != nf:
                cnt['frame_count_differs'] += 1
            if pr['shape'][:2] != d['size_csv']:
                cnt['size_differs'] += 1
            if abs(pr['fps'] - d['fps_csv']) > 0.5:
                cnt['fps_differs'] += 1
            if not pr['grey']:
                cnt['not_grey'] += 1
        if len(frames) != 2:
            cnt['traced_frames_not_2'] += 1
        if any(k >= nf for k in frames):
            cnt['traced_frame_beyond_video'] += 1
        if lines and any(m != 21 for m in lines):
            cnt['lines_not_21'] += 1
        if len(frames) == 2:
            order = list(tr[n])                               # frame order as listed in the CSV
            a = {k: polygon_area(np.array(tr[n][k])) for k in frames}
            big, small = max(frames, key=a.get), min(frames, key=a.get)
            d.update(area_px={str(k): round(v, 1) for k, v in a.items()}, large_frame=big, small_frame=small,
                     large_listed_first=order[0] == big, large_before_small=big < small,
                     ef_area_proxy=round(100 * (1 - (a[small] / a[big]) ** 1.5), 2))
        per.append(d)

    two = [d for d in per if 'ef_area_proxy' in d]
    ef_true = np.array([d['ef'] for d in two])
    ef_prox = np.array([d['ef_area_proxy'] for d in two])
    fps = defaultdict(int)
    for d in per:
        fps[d['fps_csv']] += 1
    nfr = [d['frames_csv'] for d in per]
    summary = dict(
        filelist_rows=len(rows), videos_on_disk=len(videos), rows_without_video=len(missing),
        videos_not_in_filelist=extra[:20], split=dict(split), ef_inconsistent_or_out_of_range=len(ef_bad),
        ef_min_mean_max=[float(min(d['ef'] for d in per)), round(float(np.mean([d['ef'] for d in per])), 2),
                         float(max(d['ef'] for d in per))],
        frames_min_median_max=[min(nfr), int(np.median(nfr)), max(nfr)],
        fps_csv_counts={str(k): v for k, v in sorted(fps.items())},
        traced_videos=len(tr), traced_videos_not_in_filelist=len(set(tr) - set(names)),
        counts=dict(cnt),
        traced_two_frames=len(two),
        large_listed_first_fraction=round(float(np.mean([d['large_listed_first'] for d in two])), 4),
        large_before_small_fraction=round(float(np.mean([d['large_before_small'] for d in two])), 4),
        ef_area_proxy_vs_ef=dict(pearson=round(float(np.corrcoef(ef_true, ef_prox)[0, 1]), 4),
                                 mean_abs_diff=round(float(np.mean(np.abs(ef_true - ef_prox))), 2)),
        seconds=round(time.time() - t0, 1))
    if cnt['decode_failed']:
        problems.append('%d videos could not be decoded' % cnt['decode_failed'])
    report = dict(data=str(DATA), summary=summary, ef_bad=ef_bad[:50], problems=problems, per_video=per)
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / 'results' / 'data_check.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(summary, indent=1))
    print('\n'.join(problems) or 'NO BLOCKING PROBLEMS')
    print('ECHONET_DATA_OK' if not problems else 'ECHONET_DATA_PROBLEMS', flush=True)
    sys.exit(0 if not problems else 1)


if __name__ == '__main__':
    main()
