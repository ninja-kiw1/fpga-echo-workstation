"""Select mixed activation precision using calibration images only.

Screen every stored arithmetic node on two fixed calibration images, confirm
top candidates on all eight, freeze selection, then evaluate development holdout.
"""
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np
from PIL import Image,ImageDraw
import torch
from models.get_model import get_arch
from int8_reference import build_plan,fuse_conv_affine,run_integer,Observer,self_test
from quantize_stage1 import read_input,score

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'results/stage1_int8_v2'
OUT=ROOT/'results/quant_sensitivity'
DATA=ROOT.parent/'medical_fpga/data/stare/png'


def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,indent=2),encoding='utf-8')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(4)
    print(self_test(),flush=True)
    base=json.loads((SOURCE/'summary.json').read_text(encoding='utf-8'))
    split=base['split']; calibration=split['calibration']; holdout=split['holdout']
    # Fixed BEFORE any accuracy calculations; calibration-only representative subset.
    screen_ids=[calibration[0],calibration[-1]]
    original_plan=json.loads((ROOT/'results/stage1_int8_v1/quant_plan.json').read_text(encoding='utf-8'))
    ranges={s['name']:s['calibration_absmax'] for s in original_plan}
    model=get_arch('unet').eval()
    model.load_state_dict(torch.load(ROOT/'results/model_comparison/stage1_checkpoint.pth',weights_only=True)['model_state_dict'])
    graph=torch.fx.symbolic_trace(model)
    def make(names):
        p,a=build_plan(graph,ranges,{n:16 for n in names})
        return fuse_conv_affine(graph,p,a)
    plan,arrays=make([])
    old_plan=json.loads((SOURCE/'quant_plan.json').read_text(encoding='utf-8'))
    with np.load(SOURCE/'parameters.npz',allow_pickle=False) as z:
        assert set(arrays)==set(z.files)
        assert all(np.array_equal(arrays[k],z[k]) for k in arrays)
    assert all(s['scale']==t['scale'] for s,t in zip(plan,old_plan))
    cached={}
    def data(name):
        if name not in cached:
            rgb,q,x=read_input(DATA/'images'/(name+'.png'))
            label=Image.open(DATA/'labels_ah'/(name+'.png')).convert('L').resize((512,512),Image.Resampling.NEAREST)
            cached[name]=(rgb,q,x,np.array(label)>127,label)
        return cached[name]
    def evaluate(names,ids):
        p,a=make(names); qt=math.floor(math.log(.4196/.5804)/p[-1]['scale'])+1
        rows=[]
        for name in ids:
            rgb,q,x,target,label=data(name)
            result,_=run_integer(p,a,q)
            metrics=score(result[0,0].numpy()>=qt,target)
            rows.append(dict(image=name,**metrics))
            if not names:
                assert np.array_equal(result[0,0].numpy(),np.load(SOURCE/name/'output_int8.npy'))
        return dict(nodes=names,mean_dice=statistics.mean(r['dice'] for r in rows),per_image=rows)
    candidates=[s['name'] for s in plan if s['op'] not in ('Input','MaxPool2d','ReLU')]
    protocol=dict(screen_ids=screen_ids,confirmation_ids=calibration,
                  selection='Screen all stored arithmetic outputs individually at INT16 on two fixed calibration images; confirm best three and combination of best two on all eight; select highest calibration mean Dice, tie-break fewer promoted nodes. No holdout selection.',
                  candidate_nodes=candidates,split=split,threshold=.4196,
                  scope='Same eight-image ranges, INT8 weights; activation precision only. Holdout is development-only.')
    save('protocol.json',protocol)
    screen=[]
    with torch.inference_mode():
        baseline=evaluate([],screen_ids)
        print(f"Screen baseline {baseline['mean_dice']:.6f}",flush=True)
        for i,node in enumerate(candidates):
            row=evaluate([node],screen_ids)
            row['gain']=row['mean_dice']-baseline['mean_dice']
            screen.append(row); save('screening.json',screen)
            print(f"Screen {i+1}/{len(candidates)} {node}: gain {row['gain']:+.6f}",flush=True)
        ranking=sorted(screen,key=lambda r:r['mean_dice'],reverse=True)
        confirm=[evaluate([],calibration)]
        for r in ranking[:3]:
            row=evaluate(r['nodes'],calibration);confirm.append(row)
            print(f"Confirm {r['nodes']}: Dice {row['mean_dice']:.6f}",flush=True)
        combo=ranking[0]['nodes']+ranking[1]['nodes']
        row=evaluate(combo,calibration);confirm.append(row)
        print(f"Confirm combination: Dice {row['mean_dice']:.6f}",flush=True)
        save('confirmation.json',confirm)
        chosen=sorted(confirm,key=lambda r:(-r['mean_dice'],len(r['nodes'])))[0]
        selected=chosen['nodes']; plan,arrays=make(selected)
        # Persist and hash frozen choice BEFORE any holdout evaluation.
        save('selection.json',dict(nodes=selected,calibration_mean_dice=chosen['mean_dice'],selection_source='calibration only',holdout_evaluated=False))
        save('quant_plan.json',plan);np.savez(OUT/'parameters.npz',**arrays)
        with np.load(OUT/'parameters.npz',allow_pickle=False) as z:
            arrays={k:z[k] for k in z.files}
        plan=json.loads((OUT/'quant_plan.json').read_text(encoding='utf-8'))
        qt=math.floor(math.log(.4196/.5804)/plan[-1]['scale'])+1
        results=[]; layer_stats={}
        for name in sorted(calibration+holdout):
            rgb,q,x,target,label=data(name)
            dest=OUT/name;dest.mkdir(exist_ok=True)
            golden=name==holdout[0]
            fp=Observer(graph,capture=golden);logits=fp.run(x)
            gold=dest/'golden_tensors' if golden else None
            if gold: gold.mkdir(exist_ok=True)
            result,stats=run_integer(plan,arrays,q,fp.tensors,gold)
            layer_stats[name]=stats
            pred=result[0,0].numpy()>=qt
            assert np.array_equal(pred,(result.double()*plan[-1]['scale']).sigmoid()[0,0].numpy()>.4196)
            fm=score(logits.sigmoid()[0,0].numpy()>.4196,target)
            qm=score(pred,target)
            old=next(r for r in base['per_image'] if r['image']==name)
            assert abs(fm['dice']-old['fp32']['dice'])<1e-12
            dtype='int16' if plan[-1]['qmax']>127 else 'int8'
            np.save(dest/'output_quantized.npy',result[0,0].numpy().astype(dtype))
            mask=Image.fromarray(pred.astype('uint8')*255);mask.save(dest/'mask.png')
            canvas=Image.new('RGB',(2048,548),'white'); draw=ImageDraw.Draw(canvas)
            for i,(title,im) in enumerate([('RGB',rgb),(f"FP32 Dice {fm['dice']:.4f}",Image.fromarray((logits.sigmoid()[0,0].numpy()>.4196).astype('uint8')*255)),
                                         (f"Mixed Dice {qm['dice']:.4f}",mask),('AH',label)]):
                draw.text((i*512+8,10),title,fill='black');canvas.paste(im.convert('RGB'),(i*512,36))
            canvas.save(dest/'comparison.png')
            r=dict(image=name,group='calibration' if name in calibration else 'holdout',fp32=fm,quantized=qm,
                   dice_drop=fm['dice']-qm['dice'],v2_dice=old['int8']['dice'])
            results.append(r)
            print(f"Final {name}: FP32 {fm['dice']:.4f}, mixed {qm['dice']:.4f}, drop {r['dice_drop']:+.4f}",flush=True)
        groups={}
        for group in ('calibration','holdout','all'):
            subset=[r for r in results if group=='all' or r['group']==group]
            groups[group]=dict(count=len(subset),fp32_dice=statistics.mean(r['fp32']['dice'] for r in subset),
                               quantized_dice=statistics.mean(r['quantized']['dice'] for r in subset),
                               dice_drop=statistics.mean(r['dice_drop'] for r in subset),
                               fp32_iou=statistics.mean(r['fp32']['iou'] for r in subset),
                               quantized_iou=statistics.mean(r['quantized']['iou'] for r in subset))
        shapes={r['name']:r['elements'] for r in layer_stats[holdout[0]]}
        promoted=[s for s in plan if s['qmax']>127 and s['op']!='Input']
        hardware=[]
        for s in promoted:
            hardware.append(dict(node=s['name'],elements=shapes[s['name']],extra_tensor_bytes=shapes[s['name']],
                                 consumers=[dict(name=t['name'],op=t['op']) for t in plan if s['name'] in t['inputs']]))
        summary=dict(selected_nodes=selected,actual_int16_nodes=[s['name'] for s in promoted],groups=groups,
                     threshold_probability=.4196,output_scale=plan[-1]['scale'],output_integer_threshold_ge=qt,
                     hardware=hardware,max_accumulator_abs_bound=max(s.get('accumulator_abs_bound',0) for s in plan),
                     worst=max(results,key=lambda r:r['dice_drop']),
                     passes_provisional_mean_target=groups['holdout']['dice_drop']<=.01,
                     split=split,per_image=results,
                     plan_sha256=hashlib.sha256((OUT/'quant_plan.json').read_bytes()).hexdigest(),
                     parameters_sha256=hashlib.sha256((OUT/'parameters.npz').read_bytes()).hexdigest(),
                     scope='Calibration-selected mixed precision; developmental holdout only, no FPGA validation.')
        save('summary.json',summary);save('layer_errors.json',layer_stats)
        lines=['# 量化逐节点敏感度与混合位宽验证','',
               '所有候选均保持原权重、校准范围、模型结构和阈值。仅将一个存储节点的激活从 INT8 提高到 INT16，池化等保尺度节点随输入继承位宽。权重仍全部 INT8。','',
               '先在预先固定的两张校准图上逐节点筛选，再在全部 8 张校准图上确认前三名及前两名组合，冻结校准集平均 Dice 最优方案后才运行 12 张开发留出图。此结论不是独立测试性能。','',
               f"两张筛选图：{', '.join(screen_ids)}。",'',
               f"选择节点：{', '.join(selected) or '无（保持 v2）'}。",'',
               '| 子集 | 张数 | FP32 Dice | 混合位宽 Dice | 下降 |','|---|---:|---:|---:|---:|']
        for g,v in groups.items():
            lines.append(f"| {g} | {v['count']} | {v['fp32_dice']:.6f} | {v['quantized_dice']:.6f} | {v['dice_drop']:+.6f} |")
        lines+=['',f"暂定开发留出平均下降≤0.01：{'通过' if summary['passes_provisional_mean_target'] else '未通过'}。",'',
                f"最差单张：{summary['worst']['image']}，下降 {summary['worst']['dice_drop']:.6f}。平均门禁不保证每张图都满足同样误差。",'',
                '## 硬件影响','',
                f"输出 scale={plan[-1]['scale']:.12g}，整数分割条件 q>={qt}。",'',
                '提升位宽不改变卷积 MAC 数。若 INT16 节点后接卷积，则该层需要 INT16×INT8 乘法；不是免费增加精度。INT32 累加和 INT64 重缩放静态范围检查仍必须通过。','']
        for r in hardware:
            lines.append(f"- {r['node']}：新增单张张量存储 {r['extra_tensor_bytes']:,} 字节；后继 {r['consumers']}。")
        lines+=['','这些字节数按单张张量计算，不能直接当作整网峰值 BRAM/DDR 或每帧实际流量；是否落 DDR、读几次由调度决定。','',
                '## 筛选排名（两张校准图，不是最终精度）','','| 节点 | 平均 Dice | 相比 v2 增益 |','|---|---:|---:|']
        for r in ranking:
            lines.append(f"| {r['nodes'][0]} | {r['mean_dice']:.6f} | {r['gain']:+.6f} |")
        lines+=['','## 8 张校准图确认','','| 节点组合 | Dice |','|---|---:|']
        for r in confirm:
            lines.append(f"| {', '.join(r['nodes']) or 'v2 baseline'} | {r['mean_dice']:.6f} |")
        lines+=['','## 逐图结果','','| 图像 | 子集 | FP32 | v2 INT8 | 混合位宽 |','|---|---|---:|---:|---:|']
        for r in results:
            lines.append(f"| [{r['image']}]({r['image']}/comparison.png) | {r['group']} | {r['fp32']['dice']:.4f} | {r['v2_dice']:.4f} | {r['quantized']['dice']:.4f} |")
        (OUT/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in summary.items() if k!='per_image'},indent=2),flush=True)


if __name__=='__main__':
    main()
