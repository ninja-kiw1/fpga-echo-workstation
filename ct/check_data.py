"""⑤ CT: integrity check of the downloaded MSD Task09 Spleen (E:\\PLD\\Task09_Spleen\\Task09_Spleen).

Every .nii.gz is decompressed in full (gzip CRC checked) and its NIfTI-1 header parsed:
shape, voxel size, data type, scaling, orientation (sform), intensity range.
Training images are compared with their labels (same grid), and the labels must be {0, 1}.
No nibabel needed. Result: results/data_check.json.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I check_data.py
"""
import gzip
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'Task09_Spleen' / 'Task09_Spleen'
DTYPES = {2: np.uint8, 4: np.int16, 8: np.int32, 16: np.float32, 64: np.float64,
          256: np.int8, 512: np.uint16, 768: np.uint32}


def read_nifti(path):
    raw = gzip.decompress(path.read_bytes())
    if struct.unpack_from('<i', raw, 0)[0] != 348 or raw[344:347] not in (b'n+1', b'ni1'):
        raise ValueError('not a little-endian NIfTI-1 file')
    dim = struct.unpack_from('<8h', raw, 40)
    datatype = struct.unpack_from('<h', raw, 70)[0]
    pixdim = struct.unpack_from('<8f', raw, 76)
    vox_offset = int(struct.unpack_from('<f', raw, 108)[0])
    slope, inter = struct.unpack_from('<2f', raw, 112)
    qform_code, sform_code = struct.unpack_from('<2h', raw, 252)
    srow = np.array(struct.unpack_from('<12f', raw, 280), np.float64).reshape(3, 4)
    shape = tuple(int(d) for d in dim[1:1 + dim[0]])
    n = int(np.prod(shape))
    dt = np.dtype(DTYPES[datatype])
    if len(raw) < vox_offset + n * dt.itemsize:
        raise ValueError('truncated data: %d < %d' % (len(raw), vox_offset + n * dt.itemsize))
    data = np.frombuffer(raw, dt, n, vox_offset).reshape(shape[::-1])      # C order: [z, y, x]
    hdr = dict(shape_xyz=list(shape), voxel_mm=[round(float(p), 4) for p in pixdim[1:1 + dim[0]]],
               dtype=dt.name, slope=float(slope), inter=float(inter), qform_code=int(qform_code),
               sform_code=int(sform_code), sform=np.round(srow, 4).tolist(), bytes_gz=path.stat().st_size)
    return hdr, data


def main():
    t0 = time.time()
    meta = json.loads((DATA / 'dataset.json').read_text(encoding='utf-8'))
    report = dict(data=str(DATA), dataset_json={k: meta[k] for k in ('name', 'reference', 'licence', 'release',
                                                                      'modality', 'labels', 'numTraining', 'numTest')},
                  training=[], test=[], problems=[])
    listed_tr = sorted(Path(e['image']).name for e in meta['training'])
    listed_ts = sorted(Path(e).name for e in meta['test'])
    on_disk_tr = sorted(p.name for p in (DATA / 'imagesTr').glob('*.nii.gz'))
    on_disk_lb = sorted(p.name for p in (DATA / 'labelsTr').glob('*.nii.gz'))
    on_disk_ts = sorted(p.name for p in (DATA / 'imagesTs').glob('*.nii.gz'))
    for what, a, b in (('imagesTr', listed_tr, on_disk_tr), ('labelsTr', listed_tr, on_disk_lb), ('imagesTs', listed_ts, on_disk_ts)):
        if a != b:
            report['problems'].append('%s: dataset.json and disk differ: missing %s, extra %s'
                                      % (what, sorted(set(a) - set(b)), sorted(set(b) - set(a))))
    for name in on_disk_tr:
        try:
            hi, img = read_nifti(DATA / 'imagesTr' / name)
            hl, lab = read_nifti(DATA / 'labelsTr' / name)
        except Exception as e:                                            # noqa: BLE001
            report['problems'].append('%s: %s' % (name, e))
            print(name, 'ERROR', e, flush=True)
            continue
        hu = img.astype(np.float32) * (hi['slope'] or 1.0) + hi['inter']
        vals = np.unique(lab)
        z = np.nonzero(lab.reshape(lab.shape[0], -1).any(1))[0]
        vox_ml = float(np.prod(hi['voxel_mm'])) / 1000.0
        row = dict(case=name, image=hi, label=dict(dtype=hl['dtype'], values=vals.tolist()),
                   hu_min=float(hu.min()), hu_max=float(hu.max()),
                   spleen_voxels=int((lab == 1).sum()), spleen_ml=round(float((lab == 1).sum()) * vox_ml, 1),
                   spleen_slices=int(len(z)), spleen_z=[int(z.min()), int(z.max())] if len(z) else None)
        if hi['shape_xyz'] != hl['shape_xyz'] or hi['voxel_mm'] != hl['voxel_mm'] or hi['sform'] != hl['sform']:
            report['problems'].append('%s: image and label grids differ' % name)
        if not set(vals.tolist()) <= {0, 1} or row['spleen_voxels'] == 0:
            report['problems'].append('%s: label values %s, spleen voxels %d' % (name, vals.tolist(), row['spleen_voxels']))
        report['training'].append(row)
        print(name, hi['shape_xyz'], hi['voxel_mm'], 'HU %.0f..%.0f' % (row['hu_min'], row['hu_max']),
              'spleen %.0f mL on %d slices' % (row['spleen_ml'], row['spleen_slices']), '(%.0fs)' % (time.time() - t0), flush=True)
    for name in on_disk_ts:
        try:
            hi, img = read_nifti(DATA / 'imagesTs' / name)
        except Exception as e:                                            # noqa: BLE001
            report['problems'].append('%s: %s' % (name, e))
            continue
        hu = img.astype(np.float32) * (hi['slope'] or 1.0) + hi['inter']
        report['test'].append(dict(case=name, image=hi, hu_min=float(hu.min()), hu_max=float(hu.max())))
        print(name, hi['shape_xyz'], hi['voxel_mm'], flush=True)

    tr = report['training']
    sl = [r['image']['shape_xyz'][2] for r in tr + report['test']]
    report['summary'] = dict(
        training_cases=len(tr), test_cases_without_labels=len(report['test']),
        in_plane=sorted({tuple(r['image']['shape_xyz'][:2]) for r in tr + report['test']}),
        slices_min_max=[min(sl), max(sl)], slices_total=int(sum(sl)),
        voxel_xy_mm_min_max=[min(r['image']['voxel_mm'][0] for r in tr + report['test']),
                             max(r['image']['voxel_mm'][0] for r in tr + report['test'])],
        voxel_z_mm_min_max=[min(r['image']['voxel_mm'][2] for r in tr + report['test']),
                            max(r['image']['voxel_mm'][2] for r in tr + report['test'])],
        dtypes=sorted({r['image']['dtype'] for r in tr + report['test']}),
        sform_axis_signs=sorted({tuple(int(np.sign(r['image']['sform'][i][i])) for i in range(3)) for r in tr + report['test']}),
        spleen_ml_min_median_max=[min(r['spleen_ml'] for r in tr), float(np.median([r['spleen_ml'] for r in tr])),
                                  max(r['spleen_ml'] for r in tr)],
        slices_with_spleen_total=int(sum(r['spleen_slices'] for r in tr)),
        problems=len(report['problems']), seconds=round(time.time() - t0, 1))
    (ROOT / 'results').mkdir(exist_ok=True)
    (ROOT / 'results' / 'data_check.json').write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    print('\n'.join(report['problems']) or 'NO PROBLEMS')
    print('CT_DATA_OK' if not report['problems'] else 'CT_DATA_PROBLEMS', flush=True)
    sys.exit(0 if not report['problems'] else 1)


if __name__ == '__main__':
    main()
