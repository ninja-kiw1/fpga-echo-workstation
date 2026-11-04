"""Check the whole-network C-simulation outputs and derive masks and Dice.

Run after run_csim.tcl (or the csim executable) has written
vectors/images/<img>/final_actual.bin for every image:

    & 'E:/PLD/lwnet/.venv/Scripts/python.exe' verify_network.py

For every image: the HLS output must equal lwnet output_quantized.npy; the mask
(q >= integer threshold) must equal lwnet mask.png; Dice against the AH label
must reproduce the lwnet summary. Writes results/report.md, verification.json
and comparison.png.
"""
from pathlib import Path
import argparse
import json
import sys

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / 'unet_conv'))
sys.path.insert(0, str(PLD / 'lwnet'))
from generate_vectors import DEFAULT_QUANT  # noqa: E402
from quantize_stage1 import score  # noqa: E402  (same Dice definition as lwnet)

STARE = PLD / 'medical_fpga' / 'data' / 'stare' / 'png'


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--quant-dir', type=Path, default=DEFAULT_QUANT)
    parser.add_argument('--vectors', type=Path, default=HERE / 'vectors')
    parser.add_argument('--output', type=Path, default=HERE / 'results')
    args = parser.parse_args()
    summary = json.loads((args.quant_dir / 'summary.json').read_text(encoding='utf-8'))
    threshold = summary['output_integer_threshold_ge']
    lwnet_rows = {r['image']: r for r in summary['per_image']}
    names = (args.vectors / 'images.txt').read_text(encoding='ascii').split()
    rows, failures = [], []
    for name in names:
        q = np.fromfile(args.vectors / 'images' / name / 'final_actual.bin', dtype=np.int8)
        if q.size != 512 * 512:
            raise ValueError(f'{name}: final_actual.bin has {q.size} values')
        q = q.reshape(512, 512)
        ref = np.load(args.quant_dir / name / 'output_quantized.npy')
        mask = q >= threshold
        ref_mask = np.array(Image.open(args.quant_dir / name / 'mask.png')) > 127
        label = np.array(Image.open(STARE / 'labels_ah' / f'{name}.png').convert('L')
                         .resize((512, 512), Image.Resampling.NEAREST)) > 127
        s = score(mask, label)
        expected = lwnet_rows[name]['quantized']
        row = dict(image=name, group=lwnet_rows[name]['group'],
                   output_mismatches=int((q != ref).sum()), mask_mismatches=int((mask != ref_mask).sum()),
                   dice=s['dice'], iou=s['iou'], tp=s['tp'], fp=s['fp'], fn=s['fn'],
                   lwnet_dice=expected['dice'], fp32_dice=lwnet_rows[name]['fp32']['dice'])
        row['matches_lwnet'] = (row['output_mismatches'] == 0 and row['mask_mismatches'] == 0 and
                                all(s[k] == expected[k] for k in ('tp', 'fp', 'fn')))
        if not row['matches_lwnet']:
            failures.append(name)
        rows.append(row)

    args.output.mkdir(parents=True, exist_ok=True)
    groups = {}
    for g in ('calibration', 'holdout'):
        subset = [r for r in rows if r['group'] == g]
        if subset:
            groups[g] = dict(count=len(subset), dice=float(np.mean([r['dice'] for r in subset])),
                             fp32_dice=float(np.mean([r['fp32_dice'] for r in subset])))
    groups['all'] = dict(count=len(rows), dice=float(np.mean([r['dice'] for r in rows])),
                         fp32_dice=float(np.mean([r['fp32_dice'] for r in rows])))
    result = dict(threshold_integer_ge=threshold, images=len(rows), failures=failures,
                  all_match_lwnet=not failures, groups=groups, per_image=rows,
                  scope='HLS C simulation of unet_conv + unet_eltwise; not RTL, not board measurement.')
    (args.output / 'verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')

    # Visual check: RGB, HLS mask, AH label and differences for the best and worst images.
    picks = [max(rows, key=lambda r: r['dice'])['image'], min(rows, key=lambda r: r['dice'])['image']]
    tiles = []
    for name in picks:
        rgb = np.array(Image.open(STARE / 'images' / f'{name}.png').convert('RGB')
                       .resize((512, 512), Image.Resampling.BILINEAR))
        q = np.fromfile(args.vectors / 'images' / name / 'final_actual.bin', dtype=np.int8).reshape(512, 512)
        mask = q >= threshold
        label = np.array(Image.open(STARE / 'labels_ah' / f'{name}.png').convert('L')
                         .resize((512, 512), Image.Resampling.NEAREST)) > 127
        diff = np.zeros((512, 512, 3), np.uint8)
        diff[mask & label] = (255, 255, 255)
        diff[mask & ~label] = (230, 60, 60)    # false positive
        diff[~mask & label] = (60, 140, 255)   # missed vessel
        gray = lambda m: np.repeat((m * 255).astype(np.uint8)[:, :, None], 3, axis=2)
        tiles.append(np.concatenate([rgb, gray(mask), gray(label), diff], axis=1))
    Image.fromarray(np.concatenate(tiles, axis=0)).save(args.output / 'comparison.png')

    lines = [
        '# 整网 HLS C 仿真验证结果', '',
        '范围：unet_conv 与 unet_eltwise 两个 HLS 算子按 `vectors/network.txt` 逐层执行 30 个节点，'
        '在 C 仿真中处理全部 STARE 图像。不是 RTL 仿真，也不是板上结果。', '',
        f"- 图像数：{len(rows)}；与 lwnet 整数参考逐值一致：{len(rows) - len(failures)}/{len(rows)}。",
        f"- 血管判定：`final >= {threshold}`（对应概率阈值 {summary['threshold_probability']}）。",
        f"- 平均 Dice（AH 标注，512×512 全画布）：全部 {groups['all']['dice']:.6f}，FP32 {groups['all']['fp32_dice']:.6f}。",
    ]
    for g in ('calibration', 'holdout'):
        if g in groups:
            lines.append(f"- {g}（{groups[g]['count']} 张）：HLS {groups[g]['dice']:.6f}，FP32 {groups[g]['fp32_dice']:.6f}。")
    lines += ['', '全部 STARE 图都参与过 lwnet 的模型选择，这里的 Dice 属于开发验证，不是独立测试或医学有效性结论。', '',
              '| 图像 | 划分 | 输出不同值 | 掩膜不同像素 | HLS Dice | lwnet 整数 Dice | FP32 Dice |',
              '|---|---|---:|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['image']} | {r['group']} | {r['output_mismatches']} | {r['mask_mismatches']} | "
                     f"{r['dice']:.4f} | {r['lwnet_dice']:.4f} | {r['fp32_dice']:.4f} |")
    lines += ['', f'[对比图](comparison.png)：上行 {picks[0]}（Dice 最高），下行 {picks[1]}（Dice 最低）；'
              '从左到右为 RGB 输入、HLS 掩膜、AH 标注、差异（白=正确血管，红=误检，蓝=漏检）。', '',
              '仅用于工程教学与算法演示，不作为临床诊断依据。']
    (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"{len(rows)} images, {len(failures)} mismatching; mean Dice {groups['all']['dice']:.6f}")
    if failures:
        print('Mismatch:', failures)
        sys.exit(1)


if __name__ == '__main__':
    main()
