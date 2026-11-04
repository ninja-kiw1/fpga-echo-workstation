"""Fixed split, max-absolute PTQ, explicit integer-reference evaluation."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np
from PIL import Image,ImageDraw
import torch
from models.get_model import get_arch
from int8_reference import Observer,build_plan,run_integer,self_test,fuse_conv_affine

ROOT=Path(__file__).resolve().parent
DATA=ROOT.parent/'medical_fpga/data/stare/png'


def read_input(path):
    im=Image.open(path).convert('RGB').resize((512,512),Image.Resampling.BILINEAR)
    q=torch.from_numpy(np.array(im).transpose(2,0,1).copy()).unsqueeze(0)
    return im,q,q.float()/255


def score(pred,target):
    tp=int((pred&target).sum()); fp=int((pred&~target).sum()); fn=int((~pred&target).sum())
    return dict(dice=2*tp/(2*tp+fp+fn),iou=tp/(tp+fp+fn),precision=tp/max(tp+fp,1),recall=tp/max(tp+fn,1),tp=tp,fp=fp,fn=fn)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--fused',action='store_true',help='Keep Conv/ReLU/BN intermediate in accumulator precision')
    args=parser.parse_args(); out=args.output
    version='stage1_int8_v2' if args.fused else 'stage1_int8_v1'
    out=out or ROOT/'results'/version
    out.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(4)
    tests=self_test(); print(tests,flush=True)
    files=sorted((DATA/'images').glob('*.png'))
    assert len(files)==20 and all((DATA/'labels_ah'/p.name).is_file() for p in files)
    checkpoint=ROOT/'results/model_comparison/stage1_checkpoint.pth'
    model=get_arch('unet').eval()
    model.load_state_dict(torch.load(checkpoint,weights_only=True,map_location='cpu')['model_state_dict'],strict=True)
    graph=torch.fx.symbolic_trace(model)
    baseline=json.loads((ROOT/'results/model_comparison/summary.json').read_text(encoding='utf-8'))
    baseline_rows={r['image']:r['stage1'] for r in baseline['per_image']}
    prior=json.loads((ROOT/'results/stare_fp32/summary.json').read_text(encoding='utf-8'))
    hashes={r['image']:r for r in prior['per_image']}
    rng=np.random.default_rng(20260923)
    cal_ids=set(int(i) for i in rng.choice(len(files),8,replace=False))
    calibration=[p for i,p in enumerate(files) if i in cal_ids]
    holdout=[p for i,p in enumerate(files) if i not in cal_ids]
    split=dict(seed=20260923,calibration=[p.stem for p in calibration],holdout=[p.stem for p in holdout],
               caveat='All images previously used for model selection. Holdout excludes calibration only, not independent test data.')
    (out/'split.json').write_text(json.dumps(split,indent=2),encoding='utf-8')
    observer=Observer(graph)
    with torch.inference_mode():
        for p in calibration:
            assert hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p.stem]['image_sha256']
            observer.run(read_input(p)[2])
            print('Calibrated '+p.stem,flush=True)
    plan,arrays=build_plan(graph,observer.ranges)
    if args.fused:
        plan,arrays=fuse_conv_affine(graph,plan,arrays)
    (out/'quant_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    np.savez(out/'parameters.npz',**arrays)
    # Read the serialized artifacts back; evaluate precisely the exported parameters.
    plan=json.loads((out/'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(out/'parameters.npz',allow_pickle=False) as z:
        arrays={k:z[k] for k in z.files}
    threshold=0.4196
    logit_threshold=math.log(threshold/(1-threshold))
    output_scale=plan[-1]['scale']
    q_threshold=math.floor(logit_threshold/output_scale)+1  # >= handles strict probability > threshold
    results=[]; all_layer_stats={}; golden_id=holdout[0].stem
    with torch.inference_mode():
        for p in files:
            rgb,q,x=read_input(p)
            label_path=DATA/'labels_ah'/p.name
            assert hashlib.sha256(p.read_bytes()).hexdigest()==hashes[p.stem]['image_sha256']
            assert hashlib.sha256(label_path.read_bytes()).hexdigest()==hashes[p.stem]['label_sha256']
            label=Image.open(label_path).convert('L').resize((512,512),Image.Resampling.NEAREST)
            target=np.array(label)>127
            golden=p.stem==golden_id
            fp=Observer(graph,capture=golden)
            logits=fp.run(x)
            assert torch.equal(logits,model(x)), 'FX graph changed FP32 model output'
            destination=out/p.stem; destination.mkdir(exist_ok=True)
            dump=destination/'golden_tensors' if golden else None
            if dump: dump.mkdir(exist_ok=True)
            quant,stats=run_integer(plan,arrays,q,fp.tensors,dump)
            dequant=quant.double()*output_scale
            pred=quant[0,0].numpy()>=q_threshold
            assert np.array_equal(pred,(dequant.sigmoid()[0,0].numpy()>threshold))
            fppred=logits.sigmoid()[0,0].numpy()>threshold
            fs=score(fppred,target); qs=score(pred,target)
            assert abs(fs['dice']-baseline_rows[p.stem]['dice'])<1e-12
            np.save(destination/'output_int8.npy',quant[0,0].numpy().astype('int8'))
            Image.fromarray(pred.astype('uint8')*255).save(destination/'mask_int8.png')
            canvas=Image.new('RGB',(2048,548),'white'); draw=ImageDraw.Draw(canvas)
            for i,(name,im) in enumerate([('RGB',rgb),(f"FP32 Dice {fs['dice']:.4f}",Image.fromarray(fppred.astype('uint8')*255)),
                                         (f"INT8 Dice {qs['dice']:.4f}",Image.fromarray(pred.astype('uint8')*255)),('AH',label)]):
                draw.text((i*512+8,10),name,fill='black'); canvas.paste(im.convert('RGB'),(i*512,36))
            canvas.save(destination/'comparison.png')
            group='calibration' if p in calibration else 'holdout'
            row=dict(image=p.stem,group=group,fp32=fs,int8=qs,dice_drop=fs['dice']-qs['dice'],
                     changed_mask_fraction=float(np.mean(pred!=fppred)),
                     logit_mae=float((dequant-logits.double()).abs().mean()))
            results.append(row); all_layer_stats[p.stem]=stats
            print(f"{p.stem} {group}: FP32={fs['dice']:.4f} INT8={qs['dice']:.4f} drop={row['dice_drop']:+.4f}",flush=True)
    groups={}
    for group in ('calibration','holdout','all'):
        selected=[r for r in results if group=='all' or r['group']==group]
        groups[group]=dict(count=len(selected),fp32={k:statistics.mean(r['fp32'][k] for r in selected) for k in ('dice','iou','precision','recall')},
                          int8={k:statistics.mean(r['int8'][k] for r in selected) for k in ('dice','iou','precision','recall')},
                          mean_dice_drop=statistics.mean(r['dice_drop'] for r in selected))
    worst=max(results,key=lambda r:r['dice_drop'])
    summary=dict(version=version,method='per-output-channel symmetric weights; per-tensor max-abs activations; '+('Conv/ReLU/BN accumulator precision then one requantization' if args.fused else 'separate BN affine'),
                 split=split,groups=groups,threshold_probability=threshold,output_scale=output_scale,
                 output_integer_threshold_ge=q_threshold,golden_image=golden_id,
                 checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                 parameters_sha256=hashlib.sha256((out/'parameters.npz').read_bytes()).hexdigest(),
                 plan_sha256=hashlib.sha256((out/'quant_plan.json').read_bytes()).hexdigest(),
                 worst_dice_drop=worst,arithmetic_tests=tests,
                 max_accumulator_abs_bound=max(s.get('accumulator_abs_bound',0) for s in plan),
                 provisional_acceptance='holdout mean Dice drop <= 0.01 vs matched FP32; development criterion only',
                 passes_provisional_mean_target=groups['holdout']['mean_dice_drop']<=0.01,
                 scope='Integer arithmetic software reference; no native INT8 speed claim, no HLS/RTL/board validation.',
                 per_image=results)
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    (out/'layer_errors.json').write_text(json.dumps(all_layer_stats,indent=2),encoding='utf-8')
    lines=[f'# 第一阶段 U-Net {version} 验证', '',
           '量化路径：'+summary['method'], '',
           '现有 DRIVE 第一阶段权重，512×512 RGB，固定阈值 0.4196，AH 标注、整幅画布、无 TTA。未重新训练或调整阈值。', '',
           '固定随机种子 20260923：8 张校准，只使用图像激活范围；12 张未参与范围校准。全部 STARE 图此前均已参与模型选择，因此这里只是开发留出验证，不是独立测试结果。', '',
           '| 子集 | 张数 | FP32 Dice | INT8 Dice | Dice 下降 | FP32 IoU | INT8 IoU |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for g,v in groups.items():
        lines.append(f"| {g} | {v['count']} | {v['fp32']['dice']:.6f} | {v['int8']['dice']:.6f} | {v['mean_dice_drop']:+.6f} | {v['fp32']['iou']:.6f} | {v['int8']['iou']:.6f} |")
    lines+=['',f"暂定平均 Dice 下降 ≤0.01：{'通过' if summary['passes_provisional_mean_target'] else '未通过'}（仅针对开发留出集）。",'',
            f"单张最大下降：{worst['image']}，{worst['dice_drop']:.6f}。",'',
            f"输出 logit 的量化 scale={output_scale:.12g}；判为血管的整数条件为 q >= {q_threshold}，不需要在阈值分割硬件中计算 Sigmoid。",'',
            '## 验证', '',
            '- 有符号舍入与 Python 整数 oracle 一致；普通/转置卷积与标量 INT64 循环一致。',
            '- 每层卷积累加的保守界满足 INT32，定点乘法与 BN 仿射满足 INT64。',
            '- 使用导出后重新读入的参数做整网验证。所有图的 FP32 指标与此前基线一致。',
            '- 卷积用 float64 执行整数操作数乘加，在绝对求和界 <2^53 下精确表示；重缩放、BN、相加等均按指定整数规则运行。这不是 PyTorch 原生 INT8 加速测速。',
            f"- {golden_id} 的所有节点保存为 golden_tensors/*.npy；layer_errors.json 记录该图逐层量化误差及全体图像的饱和计数。",'',
            '## 交付文件', '',
            '- quant_plan.json：节点顺序、输入连接、scale、shift、卷积参数和累加上界。',
            '- parameters.npz：INT8 权重、INT32 bias/乘数、INT64 BN 偏置、参考 scale；这是软件交换格式，尚不是 PS/DDR 权重 blob。',
            '- split.json：冻结的校准/开发留出划分。',
            '- summary.json：逐图指标与文件哈希。', '',
            '## 逐图结果', '', '| 图像 | 子集 | FP32 Dice | INT8 Dice | 下降 |','|---|---|---:|---:|---:|']
    for r in results:
        lines.append(f"| [{r['image']}]({r['image']}/comparison.png) | {r['group']} | {r['fp32']['dice']:.4f} | {r['int8']['dice']:.4f} | {r['dice_drop']:+.4f} |")
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='per_image'},indent=2))


if __name__=='__main__':
    main()
