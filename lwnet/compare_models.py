"""Compare pretrained first-stage UNet and full WNet under a frozen protocol."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import statistics

import numpy as np
from PIL import Image, ImageDraw
import torch
from models.get_model import get_arch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results/model_comparison'
DATA = ROOT.parent / 'medical_fpga/data/stare/png'
THRESHOLD = 0.4196


def metrics(logits, target):
    prob = logits.sigmoid()[0, 0].numpy()
    assert prob.shape == (512, 512) and np.isfinite(prob).all()
    pred = prob > THRESHOLD
    tp, fp, fn = (int(np.count_nonzero(a)) for a in (pred & target, pred & ~target, ~pred & target))
    return prob, dict(dice=2*tp/(2*tp+fp+fn), iou=tp/(tp+fp+fn),
                     precision=tp/max(tp+fp, 1), recall=tp/max(tp+fn, 1), tp=tp, fp=fp, fn=fn)


def profile(model, x):
    records, handles = [], []
    def hook(name):
        def record(m, args, y):
            inp = args[0]
            mac = 0
            if isinstance(m, torch.nn.Conv2d):
                mac = y.numel() * (m.in_channels // m.groups) * np.prod(m.kernel_size)
            elif isinstance(m, torch.nn.ConvTranspose2d):
                mac = inp.numel() * (m.out_channels // m.groups) * np.prod(m.kernel_size)
            row = dict(name=name, op=type(m).__name__, input=list(inp.shape), output=list(y.shape),
                       parameters=sum(p.numel() for p in m.parameters(recurse=False)), mac=int(mac),
                       input_elements=inp.numel(), output_elements=y.numel())
            if isinstance(m, (torch.nn.Conv2d, torch.nn.ConvTranspose2d)):
                row.update(kernel=list(m.kernel_size), stride=list(m.stride), padding=list(m.padding),
                           groups=m.groups, weight_shape=list(m.weight.shape),
                           weight_elements=m.weight.numel(), bias=m.bias is not None)
            records.append(row)
        return record
    for name, m in model.named_modules():
        if not list(m.children()):
            handles.append(m.register_forward_hook(hook(name)))
    with torch.inference_mode():
        model(x)
    for h in handles:
        h.remove()
    return records


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    checkpoint_path = ROOT / 'experiments/wnet_drive/model_checkpoint.pth'
    checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=True)
    model = get_arch('wnet')
    model.load_state_dict(checkpoint['model_state_dict'], strict=True)
    model.eval()
    model.mode = 'train'  # returns both outputs; BatchNorm stays in eval mode
    assert not any(m.training for m in model.modules())
    sample = torch.zeros(1, 3, 512, 512)
    layers = profile(model, sample)
    first_layers = [r for r in layers if r['name'].startswith('unet1.')]
    profiles = {}
    for name, network, rows in [('stage1', model.unet1, first_layers), ('full', model, layers)]:
        profiles[name] = dict(parameters=sum(p.numel() for p in network.parameters()),
                              conv_macs=sum(r['mac'] for r in rows),
                              conv_weight_elements=sum(r.get('weight_elements', 0) for r in rows),
                              max_single_tensor_elements=max(max(r['input_elements'],r['output_elements']) for r in rows),
                              operator_counts=dict(Counter(r['op'] for r in rows)), layers=rows)
    (OUT / 'layer_profiles.json').write_text(json.dumps(profiles, indent=2), encoding='utf-8')
    # A standalone copy of the exact pretrained first-stage parameters, no optimizer.
    torch.save({'model_state_dict': model.unet1.state_dict(), 'source_sha256': hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                'architecture': 'get_arch(unet), RGB, channels 8/16/32, unchanged pretrained stage1'}, OUT / 'stage1_checkpoint.pth')
    standalone = get_arch('unet').eval()
    standalone.load_state_dict(torch.load(OUT / 'stage1_checkpoint.pth', weights_only=True)['model_state_dict'], strict=True)
    old = json.loads((ROOT / 'results/stare_fp32/summary.json').read_text(encoding='utf-8'))
    old_rows = {r['image']: r for r in old['per_image']}
    images = sorted((DATA / 'images').glob('*.png'))
    assert len(images) == 20
    results = []
    with torch.inference_mode():
        for path in images:
            target_path = DATA / 'labels_ah' / path.name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == old_rows[path.stem]['image_sha256']
            assert hashlib.sha256(target_path.read_bytes()).hexdigest() == old_rows[path.stem]['label_sha256']
            rgb = Image.open(path).convert('RGB').resize((512,512),Image.Resampling.BILINEAR)
            target_im = Image.open(target_path).convert('L').resize((512,512),Image.Resampling.NEAREST)
            target = np.array(target_im) > 127
            x = torch.from_numpy(np.array(rgb).transpose(2,0,1).copy()).float().unsqueeze(0)/255
            y1, y2 = model(x)
            if not results:
                assert torch.equal(y1, standalone(x)), 'Standalone export changed output'
            p1, m1 = metrics(y1, target)
            p2, m2 = metrics(y2, target)
            assert np.array_equal(p2, np.load(ROOT / 'results/stare_fp32' / path.stem / 'probability.npy'))
            assert abs(m2['dice']-old_rows[path.stem]['dice']) < 1e-12
            dest = OUT / path.stem
            dest.mkdir(exist_ok=True)
            np.save(dest / 'stage1_logits.npy', y1[0,0].numpy())
            np.save(dest / 'stage1_probability.npy',p1)
            mask1 = Image.fromarray((p1 > THRESHOLD).astype('uint8')*255)
            mask2 = Image.fromarray((p2 > THRESHOLD).astype('uint8')*255)
            mask1.save(dest / 'stage1_mask.png')
            panel = Image.new('RGB',(2048,548),'white')
            draw = ImageDraw.Draw(panel)
            for i,(name,im) in enumerate([('RGB input',rgb),(f"Stage 1 Dice {m1['dice']:.4f}",mask1),
                                         (f"Full WNet Dice {m2['dice']:.4f}",mask2),('AH annotation',target_im)]):
                draw.text((i*512+8,10),name,fill='black')
                panel.paste(im.convert('RGB'),(i*512,36))
            panel.save(dest / 'comparison.png')
            row = dict(image=path.stem,stage1=m1,full=m2,dice_gain=m2['dice']-m1['dice'])
            results.append(row)
            print(f"{path.stem} stage1={m1['dice']:.4f} full={m2['dice']:.4f} gain={row['dice_gain']:+.4f}",flush=True)
    mean = {name:{k:statistics.mean(r[name][k] for r in results) for k in ('dice','iou','precision','recall')} for name in ('stage1','full')}
    summary = dict(count=20, threshold=THRESHOLD,macro_mean=mean,
                   mean_dice_gain=mean['full']['dice']-mean['stage1']['dice'],
                   full_better_count=sum(r['dice_gain']>0 for r in results),
                   profiles={k:{a:b for a,b in v.items() if a!='layers'} for k,v in profiles.items()},
                   weights_sha256=hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
                   protocol=old['protocol'], torch=torch.__version__, per_image=results,
                   caveat='Model selection on STARE is development analysis, not independent test performance. Threshold inherited from full model; no separate stage1 tuning. MACs exclude BN/activation/pool/add/traffic. INT8 storage is hypothetical, not quantized yet.')
    (OUT / 'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    lines=['# W-Net 模型选择对比', '', '## 固定评估协议', '',
           '冻结官方 DRIVE 权重；STARE 20 张，AH 标注；RGB 直接缩放 512×512、除以 255；整幅画布评估；无 FOV 排除、无 TTA。两种模型阈值均为 0.4196，未针对第一阶段重调。', '',
           '第一阶段取现有 W-Net 的 unet1，不是重新训练的独立 U-Net。完整模型的 20 张概率输出与此前保存的基线逐值相同；导出的第一阶段权重严格加载，首张输出与原模型第一阶段逐值相同。', '',
           '## 精度与计算量', '', '| 指标 | 第一阶段 U-Net | 完整 W-Net |','|---|---:|---:|']
    for key in ('dice','iou','precision','recall'):
        lines.append(f"| 平均 {key} | {mean['stage1'][key]:.6f} | {mean['full'][key]:.6f} |")
    for key in ('parameters','conv_macs','conv_weight_elements','max_single_tensor_elements'):
        lines.append(f"| {key} | {profiles['stage1'][key]:,} | {profiles['full'][key]:,} |")
    lines += ['',f"第二阶段平均 Dice 增益：{summary['mean_dice_gain']:+.6f}；{summary['full_better_count']}/20 张有所提升。", '',
              'MAC 统计仅包含卷积和转置卷积；按实际 forward hook 捕获尺寸，转置卷积按输入像素贡献统计。BN、激活、池化、相加、拼接及 DDR 开销未计入。', '',
              '## 缓存规划（假设未来特征图为 8 位）', '',
              '- 每个 U-Net 编码器两个 skip 张量为 512×512×8（2 MiB）、256×256×16（1 MiB），合计 3 MiB。',
              '- 最大单个特征张量为 512×512×16 的解码拼接输入，即 4 MiB。第一阶段与完整网络相同；FP32 时该张量为 16 MiB。',
              '- 完整网络分阶段复用工作区，不要求为两个 U-Net 同时保留两套 skip；但第二阶段仍需 RGB（0.75 MiB）与第一阶段输出（0.25 MiB）。',
              '- 两个 4 MiB 工作缓冲 + 3 MiB skip + 最多 2 MiB 残差临时区，可作为保守起步分配，共 13 MiB；另留 RGB、阶段间结果及第二阶段拼接输入等空间。',
              '- 上述为 DDR 规划，不是已证明的最小峰值，也不是 BRAM 占用；不含对齐、DMA 双缓冲、量化参数。若将 INT32 累加整帧落 DDR，需求会明显增大。必须在 HLS 层调度定稿后验证生命周期。', '',
              '## HLS 算子边界', '',
              '普通 Conv2d(3×3/1×1)、ConvTranspose2d(2×2,stride=2)、MaxPool2d(2×2)、ReLU、BN 推理仿射、逐元素残差相加、Concat。不包含 Depthwise。主分支 Conv→ReLU→BN，不能直接按 Conv→BN 折叠。', '',
              '## 精度使用边界', '',
              '这一批数据用于候选模型选择，视为开发分析；后续量化校准和最终精度评估需明确隔离。不能据此声称独立测试性能。存储估算尚未证明 INT8 精度。', '',
              '## 逐图对比', '', '| 图像 | 第一阶段 Dice | 完整 Dice | 第二阶段增益 |','|---|---:|---:|---:|']
    for r in results:
        lines.append(f"| [{r['image']}]({r['image']}/comparison.png) | {r['stage1']['dice']:.4f} | {r['full']['dice']:.4f} | {r['dice_gain']:+.4f} |")
    (OUT / 'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    layer_lines=['# 实际调用顺序的逐算子层表', '', 'shape 顺序为 NCHW。残差 Add、Concat 为函数调用，未出现在叶子模块表中；应结合网络源码使用。', '',
                 '| 层名 | 算子 | 输入 | 输出 | MAC |','|---|---|---|---|---:|']
    for r in layers:
        layer_lines.append(f"| {r['name']} | {r['op']} | {r['input']} | {r['output']} | {r['mac']:,} |")
    (OUT / 'model_layers.md').write_text('\n'.join(layer_lines)+'\n',encoding='utf-8')
    # Show the six cases with greatest absolute differences, selected without changing threshold.
    selected=sorted(results,key=lambda r:abs(r['dice_gain']),reverse=True)[:6]
    contact=Image.new('RGB',(1024,274*len(selected)),'white')
    for i,r in enumerate(selected):
        with Image.open(OUT / r['image'] / 'comparison.png') as im:
            contact.paste(im.resize((1024,274)),(0,i*274))
        ImageDraw.Draw(contact).text((4,i*274+22),r['image'],fill='red')
    contact.save(OUT / 'largest_differences.png')
    print(json.dumps({k:v for k,v in summary.items() if k!='per_image'},indent=2))


if __name__ == '__main__':
    main()
