"""Generate independent NumPy reference vectors for HLS verification."""
from pathlib import Path
import json
import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
VECTORS = HERE / 'vectors'

def reference(image):
    padded = np.pad(image.astype(np.uint16), ((1, 1), (1, 1)), mode='edge')
    h, w = image.shape
    total = np.zeros((h, w), dtype=np.uint16)
    for dy in range(3):
        for dx in range(3):
            total += padded[dy:dy+h, dx:dx+w]
    return (total // 9).astype(np.uint8)

def main():
    VECTORS.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260922)
    tests = [
        ('black', np.zeros((8, 9), np.uint8)),
        ('white', np.full((8, 9), 255, np.uint8)),
        ('single_pixel', np.array([[137]], np.uint8)),
        ('single_row', np.arange(9, dtype=np.uint8)[None, :] * 27),
        ('single_column', np.arange(9, dtype=np.uint8)[:, None] * 27),
    ]
    for name, pos in [('impulse_center', (4, 4)), ('impulse_corner', (0, 0)),
                      ('impulse_bottom_right', (7, 8))]:
        a = np.zeros((8, 9), np.uint8); a[pos] = 255; tests.append((name, a))
    stripes = ((np.indices((13, 17)).sum(axis=0) % 2) * 255).astype(np.uint8)
    tests += [('checkerboard', stripes), ('random', rng.integers(0, 256, (32, 48), dtype=np.uint8))]
    path = HERE.parents[1] / 'data' / 'stare' / 'png' / 'images' / 'im0001.png'
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    if image is None: raise ValueError('Cannot decode STARE input')
    tests.append(('stare_im0001_green', image[:, :, 1]))
    # Final differently sized frame checks state from preceding full-size frame.
    tests.append(('after_real_frame', rng.integers(0, 256, (11, 7), dtype=np.uint8)))
    records = []
    for name, image in tests:
        expected = reference(image)
        image.tofile(VECTORS / f'{name}_input.bin')
        expected.tofile(VECTORS / f'{name}_expected.bin')
        h, w = image.shape
        records.append({'name': name, 'width': w, 'height': h, 'pixels': w*h})
    (VECTORS / 'cases.txt').write_text(''.join(f"{r['name']} {r['width']} {r['height']}\n" for r in records), encoding='ascii')
    (VECTORS / 'manifest.json').write_text(json.dumps({
        'spec': 'uint8, replicate border, 3x3 sum in uint16, floor division by 9',
        'seed': 20260922, 'cases': records
    }, indent=2), encoding='utf-8')
    print(f'Generated {len(records)} cases, {sum(r["pixels"] for r in records)} pixels.')
if __name__ == '__main__':
    main()
