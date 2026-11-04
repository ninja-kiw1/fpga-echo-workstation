"""512-square RGB smoke test. Direct resize, no FOV crop, no TTA.

This deliberately simple deployment input is not the paper evaluation pipeline.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw
import torch
from models.get_model import get_arch


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--label', type=Path)
    parser.add_argument('--weights', type=Path, default=root / 'experiments/wnet_drive/model_checkpoint.pth')
    parser.add_argument('--output', type=Path, default=root / 'results/demo')
    parser.add_argument('--threshold', type=float, default=0.4196)
    args = parser.parse_args()
    if not 0 < args.threshold < 1:
        parser.error('threshold must be between 0 and 1')
    args.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    model = get_arch('wnet')
    checkpoint = torch.load(args.weights, map_location='cpu', weights_only=True)
    model.load_state_dict(checkpoint['model_state_dict'], strict=True)
    model.mode = 'eval'
    model.eval()
    rgb = Image.open(args.image).convert('RGB').resize((512, 512), Image.Resampling.BILINEAR)
    x = torch.from_numpy(np.array(rgb).transpose(2, 0, 1).copy()).float().unsqueeze(0) / 255.0
    with torch.inference_mode():
        model(x)  # warmup
        start = time.perf_counter()
        logits = model(x)
        elapsed = time.perf_counter() - start
        prob = logits.sigmoid()[0, 0].numpy()
    assert logits.shape == (1, 1, 512, 512)
    assert np.isfinite(prob).all()
    mask = prob > args.threshold
    rgb.save(args.output / 'input_rgb_512.png')
    np.save(args.output / 'probability.npy', prob)
    np.save(args.output / 'logits.npy', logits[0, 0].numpy())
    probability = Image.fromarray(np.rint(prob * 255).astype('uint8'))
    binary = Image.fromarray(mask.astype('uint8') * 255)
    probability.save(args.output / 'probability.png')
    binary.save(args.output / 'mask.png')
    overlay = np.array(rgb).astype(np.float32)
    overlay[mask] = 0.6 * overlay[mask] + 0.4 * np.array([0, 255, 0])
    overlay = Image.fromarray(np.rint(overlay).astype('uint8'))
    overlay.save(args.output / 'overlay.png')
    panels = [('RGB input', rgb), ('Probability', probability), ('Binary mask', binary), ('Overlay', overlay)]
    metrics = {}
    if args.label:
        label = Image.open(args.label).convert('L').resize((512, 512), Image.Resampling.NEAREST)
        target = np.array(label) > 127
        label.save(args.output / 'label_512.png')
        tp = int((mask & target).sum())
        metrics = {'dice_full_resized_image': 2 * tp / max(int(mask.sum() + target.sum()), 1),
                   'iou_full_resized_image': tp / max(int((mask | target).sum()), 1)}
        panels.append(('AH annotation', label))
    canvas = Image.new('RGB', (512 * len(panels), 548), 'white')
    draw = ImageDraw.Draw(canvas)
    for i, (title, im) in enumerate(panels):
        draw.text((i * 512 + 12, 10), title, fill='black')
        canvas.paste(im.convert('RGB'), (i * 512, 36))
    canvas.save(args.output / 'comparison.png')
    report = {'image': str(args.image.resolve()), 'weights': str(args.weights.resolve()),
              'weights_sha256': hashlib.sha256(args.weights.read_bytes()).hexdigest(),
              'torch': torch.__version__, 'device': 'cpu', 'input_shape': list(x.shape),
              'parameters': sum(p.numel() for p in model.parameters()),
              'preprocessing': 'RGB, direct bilinear resize 512x512, divide by 255; no FOV crop or masking',
              'tta': False, 'threshold': args.threshold, 'forward_seconds_one_warm_run': elapsed,
              'probability_range': [float(prob.min()), float(prob.max())],
              'foreground_pixels': int(mask.sum()), **metrics,
              'scope': 'Single-image smoke test with DRIVE weights on STARE; not paper reproduction, dataset accuracy, or FPGA timing.'}
    (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
