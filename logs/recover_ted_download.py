"""Keep complete entries from the interrupted official ZIP and fetch missing items."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TED = ROOT / 'TED'
RECOVERED = TED / 'recovered' / 'TED'
API = 'https://humanheart-project.creatis.insa-lyon.fr/database/api/v1'
LOGS = ROOT / 'logs'


def curl(url, target, resume=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ['curl.exe', '-fsSL', '--retry', '4', '--retry-delay', '3',
               '--connect-timeout', '20', '--max-time', '900']
    if resume:
        command += ['--continue-at', '-']
    result = subprocess.run(command + ['-o', str(target), url], capture_output=True, text=True)
    if result.returncode == 33 and resume:
        return curl(url, target, resume=False)
    if result.returncode:
        raise RuntimeError(result.stderr)


def restore_item(item, folder):
    name = item['name']
    if Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError(name)
    target = folder / name
    if target.exists() and target.stat().st_size == item['size']:
        return
    curl(API + '/item/' + item['_id'] + '/download', target, resume=target.exists())
    if target.stat().st_size != item['size']:
        raise ValueError('Official file size mismatch: ' + str(target))


def restore_patient(folder):
    destination = RECOVERED / 'database' / folder['name']
    metadata = LOGS / ('ted_items_' + folder['name'] + '.json')
    curl(API + '/item?folderId=' + folder['_id'] + '&limit=100', metadata)
    items = json.loads(metadata.read_text())
    for item in items:
        restore_item(item, destination)
    print('Restored ' + folder['name'], flush=True)


folders = json.loads((LOGS / 'ted_database_folders.json').read_text())
pending = []
for folder in folders:
    local = RECOVERED / 'database' / folder['name']
    files = list(local.glob('*')) if local.exists() else []
    if len(files) != 5 or sum(p.stat().st_size for p in files) != folder['size']:
        pending.append(folder)
print('Recovered complete patients: %d; remaining: %d' % (len(folders) - len(pending), len(pending)), flush=True)
with ThreadPoolExecutor(max_workers=4) as executor:
    list(executor.map(restore_patient, pending))
for name, subdir in [('ted_database_items.json', 'database'), ('ted_jupyter_items.json', 'jupyter')]:
    for item in json.loads((LOGS / name).read_text()):
        restore_item(item, RECOVERED / subdir)

files = [p for p in RECOVERED.rglob('*') if p.is_file()]
expected = json.loads((LOGS / 'ted_collection.json').read_text())['size']
actual = sum(p.stat().st_size for p in files)
assert actual == expected, (actual, expected)
record = {'interrupted_download_bytes': (TED / 'ted_collection.zip.part').stat().st_size,
          'complete_patients_recovered_from_original_stream': len(folders) - len(pending),
          'patients_requiring_file_completion': len(pending), 'official_collection_bytes': expected,
          'recovered_collection_bytes': actual, 'file_count': len(files),
          'verification': '7-Zip recovered complete entries without CRC errors; missing items downloaded from official API and size checked',
          'archive_note': 'ted_collection.zip is a local reconstruction, not the original server ZIP byte stream'}
(TED / 'transfer_recovery.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
temporary = TED / 'ted_collection.reconstructed.tmp'
with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
    for path in files:
        archive.write(path, 'TED/' + path.relative_to(RECOVERED).as_posix())
temporary.rename(TED / 'ted_collection.zip')
print('Official TED collection restored and repacked', flush=True)
