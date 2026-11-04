"""Display STARE input and manual labels; no segmentation model is used."""
from pathlib import Path
import argparse
import cv2
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def read_image(path, flags):
    if not path.is_file():
        raise FileNotFoundError(f'Image not found: {path}')
    result = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), flags)
    if result is None:
        raise ValueError(f'Cannot decode: {path}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='im0001', help='STARE image ID, e.g. im0002')
    parser.add_argument('--annotator', choices=['ah', 'vk'], default='ah')
    parser.add_argument('--alpha', type=float, default=0.6, help='Manual label overlay opacity, 0 to 1')
    args = parser.parse_args()
    if not 0 <= args.alpha <= 1:
        parser.error('--alpha must be between 0 and 1')
    if not args.image.startswith('im') or not args.image[2:].isdigit():
        parser.error('--image must be an ID such as im0001')

    data = ROOT / 'data' / 'stare' / 'png'
    bgr = read_image(data / 'images' / f'{args.image}.png', cv2.IMREAD_COLOR)
    mask = read_image(data / f'labels_{args.annotator}' / f'{args.image}.png', cv2.IMREAD_GRAYSCALE)
    if bgr.shape[:2] != mask.shape:
        raise ValueError('Image and manual label dimensions do not match')
    if not np.isin(mask, [0, 255]).all():
        raise ValueError('Expected a binary manual label with values 0 and 255')

    # OpenCV reads BGR; matplotlib expects RGB. Green is channel 1 in both.
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    green = bgr[:, :, 1]
    vessels = mask == 255

    # Blend only labeled vessel pixels. Background pixels stay unchanged.
    overlay = rgb.copy()
    red = np.array([255, 0, 0], dtype=np.float32)
    overlay[vessels] = np.rint(
        (1 - args.alpha) * rgb[vessels].astype(np.float32) + args.alpha * red
    ).clip(0, 255).astype(np.uint8)

    plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(2, 2, figsize=(12, 11), facecolor='#f5f7fa')
    items = [
        (rgb, '① 原始眼底图像（RGB）', False),
        (green, '② 绿通道（灰度显示，未做增强）', True),
        (mask, f'③ 人工血管标注（{args.annotator.upper()}，白色为血管）', True),
        (overlay, f'④ 人工标注叠加（红色，透明度 {args.alpha:.0%}）', False),
    ]
    for ax, (pixels, title, grayscale) in zip(axes.flat, items):
        if grayscale:
            ax.imshow(pixels, cmap='gray', vmin=0, vmax=255, interpolation='nearest')
        else:
            ax.imshow(pixels, interpolation='nearest')
        ax.set_title(title, fontsize=13, pad=10)
        ax.axis('off')
    fig.suptitle(f'STARE 图像与人工标注对照 | {args.image}', fontsize=20, y=0.975)
    fig.text(0.5, 0.027, '人工标注不是模型预测；本图未运行分割算法。仅用于工程教学与算法演示。',
             ha='center', fontsize=11, color='#4b5563')
    fig.subplots_adjust(left=0.025, right=0.975, top=0.92, bottom=0.065, wspace=0.045, hspace=0.13)
    out = ROOT / 'outputs' / 'visualization'
    out.mkdir(parents=True, exist_ok=True)
    target = out / f'{args.image}_{args.annotator}_comparison.png'
    fig.savefig(target, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    print(f'Image: {bgr.shape[1]} x {bgr.shape[0]}, {bgr.dtype}; label: {args.annotator.upper()}')
    print(f'Manual vessel pixels: {int(vessels.sum())}; not a model prediction')
    print(f'Saved: {target}')


if __name__ == '__main__':
    main()
