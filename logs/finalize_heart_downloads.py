"""Extract official heart datasets and record transfer/integrity checks. No evaluation."""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def extract_zip(archive, destination, strip_prefix=None):
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    count = 0
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            name = info.filename.replace('\\', '/')
            relative = PurePosixPath(name)
            if relative.is_absolute() or PureWindowsPath(name).drive or '..' in relative.parts:
                raise ValueError('Unsafe archive path: ' + name)
            parts = relative.parts
            if strip_prefix and parts and parts[0] == strip_prefix:
                parts = parts[1:]
            if not parts:
                continue
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(base):
                raise ValueError('Archive path escaped output directory')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
            if target.stat().st_size != info.file_size:
                raise ValueError('Extracted size mismatch: ' + name)
            count += 1
    return count


def ted():
    root = ROOT / 'TED'
    archive = root / 'ted_collection.zip'
    count = extract_zip(archive, root, strip_prefix='TED')
    patients = sorted((root / 'database').glob('patient[0-9][0-9][0-9]'))
    assert len(patients) == 98, len(patients)
    frames = []
    types = {'MET_UCHAR': 1, 'MET_CHAR': 1, 'MET_USHORT': 2, 'MET_SHORT': 2,
             'MET_UINT': 4, 'MET_INT': 4, 'MET_FLOAT': 4, 'MET_DOUBLE': 8}
    for patient in patients:
        shapes = []
        for suffix in ('_4CH_sequence.mhd', '_4CH_sequence_gt.mhd'):
            header = patient / (patient.name + suffix)
            fields = dict(line.split('=', 1) for line in header.read_text().splitlines() if '=' in line)
            fields = {k.strip(): v.strip() for k, v in fields.items()}
            shape = tuple(map(int, fields['DimSize'].split()))
            raw = patient / fields['ElementDataFile']
            assert raw.resolve().is_relative_to(patient.resolve())
            expected = math.prod(shape) * types[fields['ElementType']] * int(fields.get('ElementNumberOfChannels', 1))
            assert raw.stat().st_size == expected, str(raw)
            shapes.append(shape)
        assert shapes[0] == shapes[1], str(patient)
        frames.append(shapes[0][-1])
    manifest = {'dataset': 'TED', 'status': 'complete', 'archive': archive.name,
                'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive),
                'zip_crc': 'passed during full extraction', 'extracted_file_count': count,
                'patient_count': len(patients), 'sequence_count': len(patients),
                'ground_truth_sequence_count': len(patients), 'mhd_raw_sizes': 'all matched',
                'frames_per_patient_min': min(frames), 'frames_per_patient_max': max(frames),
                'source': 'https://humanheart-project.creatis.insa-lyon.fr/ted.html',
                'license': 'CC BY-NC-SA 4.0; non-commercial scientific research only'}
    if (root / 'transfer_recovery.json').exists():
        manifest['transfer_recovery'] = json.loads((root / 'transfer_recovery.json').read_text())
    save(root / 'download_manifest.json', manifest)
    print(json.dumps(manifest), flush=True)


def cardiacuda():
    root = ROOT / 'CardiacUDA'
    archive = root / 'cardiacuda_kaggle.zip'
    parts = root / 'archives'
    count = extract_zip(archive, parts)
    official = json.loads((root / 'official_file_list.json').read_text(encoding='utf-8-sig'))
    for item in official['datasetFiles']:
        assert (parts / item['name']).stat().st_size == item['totalBytes'], item['name']
    last = parts / 'cardiacUDC_dataset.zip'
    if not last.exists():
        shutil.copyfile(parts / 'cardiacUDC_dataset.change2zip', last)
    tool = ROOT / 'logs' / 'download_tools' / 'x64' / '7za.exe'
    output = root / 'data'
    listing = subprocess.check_output([str(tool), 'l', '-slt', str(last)], text=True, encoding='utf-8', errors='replace')
    (root / 'archive_file_list.txt').write_text(listing, encoding='utf-8')
    entries = listing.split('----------', 1)[1]
    expected_bytes = sum(map(int, re.findall(r'^Size = (\d+)$', entries, re.MULTILINE)))
    if expected_bytes + 1024 ** 3 > shutil.disk_usage(root).free:
        raise RuntimeError('Insufficient free space for CardiacUDA extraction')
    for name in re.findall(r'^Path = (.+)$', entries, re.MULTILINE):
        posix = PurePosixPath(name.replace('\\', '/'))
        if posix.is_absolute() or PureWindowsPath(name).drive or '..' in posix.parts:
            raise ValueError('Unsafe inner archive path: ' + name)
    with (ROOT / 'logs' / 'cardiacuda_extract.log').open('w', encoding='utf-8') as log:
        subprocess.run([str(tool), 'x', str(last), '-o' + str(output), '-y', '-bd'],
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    files = [path for path in output.rglob('*') if path.is_file()]
    nifti = [path for path in files if path.name.endswith(('.nii', '.nii.gz'))]
    manifest = {'dataset': 'CardiacUDA', 'status': 'complete', 'archive': archive.name,
                'archive_bytes': archive.stat().st_size, 'archive_sha256': sha256(archive),
                'outer_zip_crc': 'passed during full extraction',
                'official_split_file_count': count, 'official_split_sizes': 'all matched',
                'inner_zip_crc': 'passed during 7-Zip extraction (exit 0)',
                'extracted_file_count': len(files), 'extracted_bytes': sum(p.stat().st_size for p in files),
                'expected_extracted_bytes': expected_bytes,
                'nifti_file_count': len(nifti), 'version': 1, 'license': 'CC BY-NC 4.0',
                'a4c_label_mapping_from_official_code': {'0': 'background', '1': 'LV', '2': 'LA', '3': 'RA', '4': 'RV'},
                'source': 'https://www.kaggle.com/datasets/xiaoweixumedicalai/cardiacudc-dataset'}
    save(root / 'download_manifest.json', manifest)
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('dataset', choices=['ted', 'cardiacuda'])
    args = parser.parse_args()
    {'ted': ted, 'cardiacuda': cardiacuda}[args.dataset]()
