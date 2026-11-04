"""Independent binary morphology reference, cross-checked against OpenCV."""
from pathlib import Path
import json
import cv2
import numpy as np
HERE = Path(__file__).resolve().parent
MODE_NAMES = ['erode', 'dilate', 'open', 'close']

def primitive(image, dilate):
    h, w = image.shape
    p = np.pad(image != 0, ((1, 1), (1, 1)), mode='edge')
    neighbors = np.stack([p[y:y+h, x:x+w] for y in range(3) for x in range(3)])
    return (np.any(neighbors, axis=0) if dilate else np.all(neighbors, axis=0)).astype(np.uint8) * 255

def reference(image, mode):
    if mode == 0: return primitive(image, False)
    if mode == 1: return primitive(image, True)
    if mode == 2: return primitive(primitive(image, False), True)
    return primitive(primitive(image, True), False)

def main():
    out = HERE / 'vectors'; out.mkdir(exist_ok=True)
    rng = np.random.default_rng(20260922)
    shapes = np.zeros((31, 37), np.uint8)
    shapes[4:19, 4:19] = 255
    shapes[10, 10] = 0
    shapes[24, 8] = 255
    shapes[3:27, 26] = 255
    shapes[15, 26] = 0
    edges = np.zeros((9, 11), np.uint8)
    edges[0:4, 0:4] = 255; edges[-3:, -3:] = 255
    patterns = [
        ('shapes', shapes), ('edges', edges),
        ('black', np.zeros((8, 9), np.uint8)), ('white', np.full((8, 9), 255, np.uint8)),
        ('one_pixel', np.array([[255]], np.uint8)),
        ('single_row', np.array([[0,255,255,255,0,255,0]], np.uint8)),
        ('single_column', np.array([[0,255,255,255,0,255,0]], np.uint8).T),
        ('nonbinary', rng.integers(0, 4, (13, 17), dtype=np.uint8)),
        ('random', (rng.random((23, 29)) > .5).astype(np.uint8) * 255)]
    label = HERE.parents[1] / 'data' / 'stare' / 'png' / 'labels_ah' / 'im0001.png'
    mask = cv2.imdecode(np.frombuffer(label.read_bytes(), np.uint8), cv2.IMREAD_GRAYSCALE)
    if mask is None: raise ValueError('Cannot read manual label')
    patterns.append(('stare_manual_ah', mask))
    tests = [(name + '_' + MODE_NAMES[mode], image, mode) for name,image in patterns for mode in range(4)]
    tests.append(('after_real_frame_open', shapes, 2))
    records = []
    kernel = np.ones((3,3), np.uint8)
    cv_modes = [cv2.MORPH_ERODE, cv2.MORPH_DILATE, cv2.MORPH_OPEN, cv2.MORPH_CLOSE]
    for name, image, mode in tests:
        expected = reference(image, mode)
        check = cv2.morphologyEx((image != 0).astype(np.uint8) * 255, cv_modes[mode],
                                kernel, borderType=cv2.BORDER_REPLICATE)
        if not np.array_equal(check, expected): raise RuntimeError('Reference disagreement: ' + name)
        image.tofile(out / f'{name}_input.bin')
        expected.tofile(out / f'{name}_expected.bin')
        h,w = image.shape
        records.append(dict(name=name,width=w,height=h,mode=mode,pixels=w*h,valid=True))
    for name,w,h,mode in [('zero_width',0,8,0),('negative_height',8,-1,1),
                           ('too_wide',701,8,2),('too_tall',8,606,3),('bad_mode',8,8,4)]:
        for kind in ('input','expected'): (out / f'{name}_{kind}.bin').write_bytes(b'')
        records.append(dict(name=name,width=w,height=h,mode=mode,pixels=0,valid=False))
    (out/'cases.txt').write_text(''.join(
        f"{r['name']} {r['width']} {r['height']} {r['mode']} {r['pixels']}\n" for r in records),encoding='ascii')
    (out/'manifest.json').write_text(json.dumps({
        'spec':'binary nonzero foreground, 3x3 square, replicate borders at each pass',
        'mode_names':MODE_NAMES,'seed':20260922,'cases':records,
        'reference_crosscheck':'NumPy reference exactly matches OpenCV with explicit replicate borders',
        'data_note':'STARE input is AH manual annotation, NOT model prediction'
    },indent=2),encoding='utf-8')
    print(f'Generated {len(records)} cases, {sum(r["pixels"] for r in records)} output pixels; OpenCV cross-check PASS.')
if __name__ == '__main__': main()
