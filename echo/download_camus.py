"""Download the public CAMUS collection from the Human Heart Project (Girder API).

Collection `CAMUS_public` (CC BY-NC-SA 4.0, non-commercial scientific research
only; mandatory citation: Leclerc et al., IEEE TMI 38(9):2198-2210, 2019,
doi 10.1109/TMI.2019.2900516). No login needed: the collection is public.

File by file into data/camus/<folder>/..., resumable: a file whose size already
matches the server's is skipped. Every file is checked against the server
size; data/camus/manifest.json records name, size and SHA-256.

    python download_camus.py
"""
import hashlib
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

API = 'https://humanheart-project.creatis.insa-lyon.fr/database/api/v1'
COLLECTION = '6373703d73e9f0047faa1bc8'
OUT = Path(__file__).resolve().parent / 'data' / 'camus'


def get(url, timeout=60):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except Exception:                       # noqa: BLE001 - retried, then raised
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))


def jget(path):
    return json.loads(get(API + path))


def walk(folder_id, rel, pool=None):
    """List (relative path, item) for every item below a folder; subfolders in parallel."""
    found = [(rel / item['name'], item) for item in jget(f'/item?folderId={folder_id}&limit=0')]
    subs = jget(f'/folder?parentType=folder&parentId={folder_id}&limit=0')
    if pool is None:
        for sub in subs:
            found += walk(sub['_id'], rel / sub['name'])
    else:
        for part in pool.map(lambda s: walk(s['_id'], rel / s['name']), subs):
            found += part
    return found


def fetch(rel, item):
    dest = OUT / rel
    if dest.exists() and dest.stat().st_size == item['size']:
        data = dest.read_bytes()
    else:
        data = get(f"{API}/item/{item['_id']}/download", timeout=300)
        if len(data) != item['size']:
            raise IOError(f'{rel}: got {len(data)} bytes, server says {item["size"]}')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return str(rel).replace('\\', '/'), item['size'], hashlib.sha256(data).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = []
    lister = ThreadPoolExecutor(16)
    for top in jget(f'/folder?parentType=collection&parentId={COLLECTION}&limit=0'):
        found = walk(top['_id'], Path(top['name']), lister)
        print(f"{top['name']}: {len(found)} files, {sum(i['size'] for _, i in found) / 1e9:.3f} GB", flush=True)
        jobs += found
    total = sum(i['size'] for _, i in jobs)
    print(f'total {len(jobs)} files, {total / 1e9:.3f} GB', flush=True)
    manifest, done, t0 = [], 0, time.time()
    with ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(fetch, rel, item) for rel, item in jobs]
        for n, fut in enumerate(as_completed(futures), 1):
            name, size, digest = fut.result()
            manifest.append(dict(file=name, size=size, sha256=digest))
            done += size
            if n % 500 == 0 or n == len(futures):
                print(f'{n}/{len(futures)} files, {done / 1e9:.2f}/{total / 1e9:.2f} GB, '
                      f'{done / 1e6 / (time.time() - t0):.1f} MB/s', flush=True)
    manifest.sort(key=lambda r: r['file'])
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=0), encoding='utf-8')
    print('DOWNLOAD_COMPLETE', len(manifest), 'files', flush=True)


if __name__ == '__main__':
    sys.exit(main())
