"""Evaluate frozen DRIVE weights on all STARE images, without threshold fitting."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=root.parent / 'medical_fpga/data/stare/png')
    parser.add_argument('--output', type=Path, default=root / 'results/stare_fp32')
    args = parser.parse_args()
    files = sorted((args.data / 'images').glob('*.png'))
    labels = sorted((args.data / 'labels_ah').glob('*.png'))
    if len(files) != 20 or {p.name for p in files} != {p.name for p in labels}:
        raise ValueError('Expected exactly 20 matched image/AH label pairs')
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for file in files:
        label = args.data / 'labels_ah' / file.name
        with Image.open(file) as a, Image.open(label) as b:
            if a.size != b.size:
                raise ValueError(f'Spatial mismatch: {file.name}')
        destination = args.output / file.stem
        subprocess.run([sys.executable, str(root / 'run_demo.py'), '--image', str(file),
                        '--label', str(label), '--output', str(destination)],
                       check=True, capture_output=True, text=True)
        report = json.loads((destination / 'report.json').read_text(encoding='utf-8'))
        probability = np.load(destination / 'probability.npy')
        pred = probability > report['threshold']
        target = np.array(Image.open(destination / 'label_512.png')) > 127
        assert probability.shape == target.shape == (512, 512)
        assert np.isfinite(probability).all()
        assert np.array_equal(pred, np.array(Image.open(destination / 'mask.png')) > 127)
        tp = int(np.count_nonzero(pred & target))
        fp = int(np.count_nonzero(pred & ~target))
        fn = int(np.count_nonzero(~pred & target))
        tn = int(np.count_nonzero(~pred & ~target))
        dice = 2 * tp / (2 * tp + fp + fn)
        iou = tp / (tp + fp + fn)
        assert abs(dice - report['dice_full_resized_image']) < 1e-12
        assert abs(iou - report['iou_full_resized_image']) < 1e-12
        row = dict(image=file.stem, dice=dice, iou=iou,
                   precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1),
                   tp=tp, fp=fp, fn=fn, tn=tn,
                   image_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                   label_sha256=hashlib.sha256(label.read_bytes()).hexdigest(),
                   forward_ms=1000 * report['forward_seconds_one_warm_run'])
        rows.append(row)
        print(f'{file.stem}: Dice={dice:.4f} IoU={iou:.4f}', flush=True)
    totals = {k: sum(r[k] for r in rows) for k in ('tp', 'fp', 'fn', 'tn')}
    macro = {k: statistics.mean(r[k] for r in rows) for k in ('dice', 'iou', 'precision', 'recall')}
    order = sorted(rows, key=lambda r: r['dice'])
    summary = dict(count=len(rows), annotation='AH', threshold=0.4196,
                   protocol='512x512 direct RGB bilinear resize /255; nearest label resize; full canvas; no FOV mask; no TTA; no threshold tuning',
                   weights_sha256=report['weights_sha256'], torch=report['torch'],
                   macro_mean=macro, dice_population_std=statistics.pstdev(r['dice'] for r in rows),
                   pooled_dice=2*totals['tp']/(2*totals['tp']+totals['fp']+totals['fn']),
                   pooled_iou=totals['tp']/(totals['tp']+totals['fp']+totals['fn']),
                   confusion_totals=totals, worst=order[0]['image'], best=order[-1]['image'],
                   median_forward_ms=statistics.median(r['forward_ms'] for r in rows),
                   scope='Frozen DRIVE checkpoint on 20 STARE images; deployment FP32 baseline, not paper protocol, clinical validation, or FPGA performance.',
                   per_image=rows)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    # Each card shows input / binary prediction / AH annotation, sorted by Dice.
    # Use four cards per row to display all 20 images.
    canvas = Image.new('RGB', (4*384, 5*160), 'white')
    draw = ImageDraw.Draw(canvas)
    for idx, row in enumerate(order):
        x, y = (idx % 4)*384, (idx // 4)*160
        draw.text((x+4, y+5), f"{row['image']}  Dice {row['dice']:.3f} | RGB / pred / AH", fill='black')
        for col, name in enumerate(('input_rgb_512.png', 'mask.png', 'label_512.png')):
            with Image.open(args.output / row['image'] / name) as im:
                canvas.paste(im.convert('RGB').resize((128, 128)), (x+col*128, y+28))
    canvas.save(args.output / 'overview.png')
    lines = ['# STARE FP32 部署基线', '',
             '使用冻结的 DRIVE 预训练权重，在 STARE 全部 20 张图上评估；人工参考为 AH。', '',
             '**固定协议：** RGB 直接缩放至 512×512、除以 255；标注最近邻缩放；整幅画布计算；无 FOV 排除、无 TTA。阈值固定 0.4196，没有在这 20 张图上搜索阈值。', '',
             f"- 逐图平均 Dice：{macro['dice']:.6f}（总体标准差 {summary['dice_population_std']:.6f}）",
             f"- 逐图平均 IoU：{macro['iou']:.6f}",
             f"- 逐图平均 Precision / Recall：{macro['precision']:.6f} / {macro['recall']:.6f}",
             f"- 合并全部像素后的 Dice / IoU：{summary['pooled_dice']:.6f} / {summary['pooled_iou']:.6f}",
             f"- 最低 / 最高 Dice：{order[0]['image']} ({order[0]['dice']:.4f}) / {order[-1]['image']} ({order[-1]['dice']:.4f})", '',
             '这是当前部署输入协议的浮点参考，不是论文复现。后续若用这些图校准量化或选择模型，应将其视为开发集，另留独立测试集；不应再称为未见测试性能。', '',
             '每张图输出浮点 logit、概率、掩膜、对比图和报告。summary.json 保存逐图指标、混淆计数、输入与权重哈希。CPU 时间仅为每图一次预热后的前向记录，不包含图像处理，不是稳定测速或 FPGA 时间。', '',
             '![全部结果，按 Dice 从低到高](overview.png)', '',
             '| 图像 | Dice | IoU | Precision | Recall |',
             '|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| [{r['image']}]({r['image']}/comparison.png) | {r['dice']:.4f} | {r['iou']:.4f} | {r['precision']:.4f} | {r['recall']:.4f} |")
    (args.output / 'report.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='per_image'}, indent=2))


if __name__ == '__main__':
    main()
