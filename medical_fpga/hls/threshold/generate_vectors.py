"""Generate fixed NumPy references, including threshold equality and invalid dimensions."""
from pathlib import Path
import json
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent

def main():
    out = HERE / 'vectors'
    out.mkdir(exist_ok=True)
    ramp = np.arange(256, dtype=np.uint8).reshape(16, 16)
    tests = [(f'ramp_t{t}', ramp, t) for t in (0, 1, 127, 128, 254, 255)]
    tests += [(f'pixel_{v}_t128', np.array([[v]], np.uint8), 128) for v in (127, 128, 129)]
    tests += [('black_t1', np.zeros((9, 8), np.uint8), 1),
              ('white_t255', np.full((9, 8), 255, np.uint8), 255)]
    rng = np.random.default_rng(20260922)
    random = rng.integers(0, 256, (19, 23), dtype=np.uint8)
    tests += [(f'consecutive_t{t}', random, t) for t in (64, 192, 64)]
    # Distinguish repeated case names while testing parameter changes across calls.
    tests[-1] = ('consecutive_t64_again', random, 64)
    image_path = HERE.parents[1] / 'data' / 'stare' / 'png' / 'images' / 'im0001.png'
    bgr = cv2.imdecode(np.frombuffer(image_path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if bgr is None: raise ValueError('Image decode failed')
    green = bgr[:, :, 1]
    tests.append(('stare_green_t128', green, 128))
    h, w = green.shape
    padded = np.pad(green.astype(np.uint16), ((1, 1), (1, 1)), mode='edge')
    sums = np.zeros((h, w), np.uint16)
    for y in range(3):
        for x in range(3):
            sums += padded[y:y+h, x:x+w]
    tests.append(('stare_mean_t128', (sums // 9).astype(np.uint8), 128))
    records = []
    for name, image, threshold in tests:
        expected = np.where(image >= threshold, 255, 0).astype(np.uint8)
        image.tofile(out / f'{name}_input.bin')
        expected.tofile(out / f'{name}_expected.bin')
        h, w = image.shape
        records.append(dict(name=name, width=w, height=h, threshold=threshold, pixels=w*h, valid=True))
    for name, w, h in [('zero_width', 0, 8), ('negative_height', 8, -1),
                       ('too_wide', 701, 8), ('too_tall', 8, 606)]:
        for kind in ('input', 'expected'):
            (out / f'{name}_{kind}.bin').write_bytes(b'')
        records.append(dict(name=name, width=w, height=h, threshold=128, pixels=0, valid=False))
    (out / 'cases.txt').write_text(''.join(
        f"{r['name']} {r['width']} {r['height']} {r['threshold']} {r['pixels']}\n" for r in records), encoding='ascii')
    (out / 'manifest.json').write_text(json.dumps({
        'spec': 'uint8 input >= uint8 threshold => 255, otherwise 0',
        'seed': 20260922, 'cases': records,
        'note': 'Mean-filtered input is an independent NumPy reference, not proof of integrated hardware execution.'
    }, indent=2), encoding='utf-8')
    print(f'Generated {len(records)} cases, {sum(r["pixels"] for r in records)} output pixels.')
if __name__ == '__main__':
    main()
