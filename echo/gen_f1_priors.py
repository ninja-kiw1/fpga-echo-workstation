"""F1 (automatic LV model) predictions on every sequence frame, as realistic priors for F2.

FP32 lv_r128 checkpoint on the 128x128 deployment inputs (seq caches). Training
split only is used for training priors; val is written for F3 experiments.

    python gen_f1_priors.py
"""
import numpy as np
import torch

from common import CACHE, RESULTS
from models.get_model import get_arch


def main():
    dev = torch.device('cuda')
    model = get_arch('unet').to(dev).eval()
    model.load_state_dict(torch.load(RESULTS / 'train' / 'lv_r128' / 'best.pth', weights_only=True)['model_state_dict'])
    for split in ('train', 'val'):
        img = np.load(CACHE / f'seq_{split}_img128.npy')
        lv = np.load(CACHE / f'seq_{split}_lv128.npy')
        out = np.zeros_like(lv)
        with torch.no_grad():
            for i in range(0, len(img), 256):
                xb = torch.from_numpy(img[i:i + 256]).to(dev).float().div(255).unsqueeze(1).expand(-1, 3, -1, -1)
                out[i:i + 256] = (model(xb)[:, 0] > 0).cpu().numpy()
        np.save(CACHE / f'seq_{split}_f1prior128.npy', out)
        inter = (out & lv).sum(axis=(1, 2))
        dice = 2 * inter / np.maximum(out.sum(axis=(1, 2)) + lv.sum(axis=(1, 2)), 1)
        print(f'{split}: {len(out)} frames, F1 prior Dice vs sequence labels (128 grid) mean {dice.mean():.4f}, '
              f'5th pct {np.percentile(dice, 5):.4f}', flush=True)


if __name__ == '__main__':
    main()
