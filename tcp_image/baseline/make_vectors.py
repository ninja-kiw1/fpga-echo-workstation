"""Inputs + expected logits for the CPU baseline (vectors/<model>_<res>.{rgb,i8}).

Expected values all come from references that were matched against the real
board value for value:
  fundus 512  medical_fpga unet_network golden im0002 (board: 0/262144 logit diff, 10-08)
  fundus 128  lwnet integer reference (quant_sensitivity plan) on im0002[::4, ::4]
  skin   128  skin/deploy/r128 golden ISIC_0000010 (board: 0/16384, 10-08)
  skin   512  integer reference of the skin plan on the full 512 image
The 128 inputs are the 512 inputs subsampled [::4, ::4], as the board does.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe make_vectors.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(PLD / 'lwnet'))
from int8_reference import run_integer  # noqa: E402


def load_plan(qdir):
    plan = json.loads((qdir / 'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(qdir / 'parameters.npz', allow_pickle=False) as z:
        return plan, {k: z[k] for k in z.files}


def ref(plan_arrays, rgb):
    q = torch.from_numpy(np.ascontiguousarray(rgb)).permute(2, 0, 1).unsqueeze(0)
    with torch.inference_mode():
        return run_integer(*plan_arrays, q)[0][0, 0].numpy()


def save(name, rgb, logits):
    out = HERE / 'vectors'
    out.mkdir(exist_ok=True)
    assert logits.min() >= -128 and logits.max() <= 127
    np.ascontiguousarray(rgb, np.uint8).tofile(out / f'{name}.rgb')
    logits.astype(np.int8).tofile(out / f'{name}.i8')
    print(name, rgb.shape, 'logit range', int(logits.min()), int(logits.max()), flush=True)


def main():
    torch.set_num_threads(8)
    gdir = PLD / 'medical_fpga/deployment/unet_network/vectors/images/im0002'
    rgb512 = np.fromfile(gdir / 'input.bin', '<i2').reshape(512, 512, 8)[:, :, :3].astype(np.uint8)
    golden = np.fromfile(gdir / 'golden/final.bin', '<i2').reshape(512, 512, 8)[:, :, 0]
    fundus = load_plan(PLD / 'lwnet/results/quant_sensitivity')
    assert np.array_equal(ref(fundus, rgb512), golden), 'fundus plan != board golden'
    save('fundus_512', rgb512, golden)
    save('fundus_128', rgb512[::4, ::4], ref(fundus, rgb512[::4, ::4]))

    skin = load_plan(PLD / 'skin/results/quant/r128')
    sdir = PLD / 'skin/deploy/r128/images/ISIC_0000010'
    rgb128 = np.fromfile(sdir / 'input.bin', '<i2').reshape(128, 128, 8)[:, :, :3].astype(np.uint8)
    exp128 = np.fromfile(sdir / 'expected_final.bin', '<i2').reshape(128, 128, 8)[:, :, 0]
    assert np.array_equal(ref(skin, rgb128), exp128)
    save('skin_128', rgb128, exp128)
    sys.path.insert(0, str(PLD / 'skin'))
    from common import load_split
    x, _, meta = load_split('train')
    full = np.array(x[next(i for i, m in enumerate(meta) if m['name'] == 'ISIC_0000010')])
    assert np.array_equal(full[::4, ::4], rgb128)
    save('skin_512', full, ref(skin, full))


if __name__ == '__main__':
    main()
