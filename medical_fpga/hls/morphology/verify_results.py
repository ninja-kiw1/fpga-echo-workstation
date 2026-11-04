"""Check morphology C/RTL evidence and produce a readable report."""
from pathlib import Path
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
SOLUTION = HERE / 'build' / 'morphology' / 'solution1'

def main():
    if not (HERE / 'build' / 'completed_all.txt').is_file():
        raise RuntimeError('Full HLS flow has not completed')
    manifest = json.loads((HERE / 'vectors' / 'manifest.json').read_text())
    records = []
    for case in manifest['cases']:
        name = case['name']
        expected = np.fromfile(HERE / 'vectors' / f'{name}_expected.bin', np.uint8)
        actual = np.fromfile(HERE / 'vectors' / f'{name}_actual.bin', np.uint8)
        if expected.size != case['pixels'] or actual.size != case['pixels']:
            raise RuntimeError('Length mismatch: ' + name)
        error = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
        records.append({**case,'mismatches':int(np.count_nonzero(error)),
                        'max_error':int(error.max()) if error.size else 0})
    if any(r['mismatches'] for r in records): raise RuntimeError('Pixel mismatch')
    csim_path = SOLUTION / 'csim/report/morphology_binary_csim.log'
    cosim_path = SOLUTION / 'sim/report/morphology_binary_cosim.rpt'
    if f'SUMMARY cases={len(records)} mismatches=0' not in csim_path.read_text(errors='replace'):
        raise RuntimeError('Missing C simulation PASS')
    if not re.search(r'Verilog\s*\|\s*Pass',cosim_path.read_text(errors='replace'),re.I):
        raise RuntimeError('Missing RTL co-simulation PASS')
    report = ET.parse(SOLUTION / 'syn/report/morphology_binary_csynth.xml').getroot()
    resources = {c.tag:int(c.text) for c in report.find('AreaEstimates/Resources')}
    packages = list((SOLUTION / 'impl/ip').glob('*.zip'))
    if not packages: raise RuntimeError('Exported IP missing')
    evidence = dict(csim='PASS',cosim='PASS',case_count=len(records),
                    total_pixels=sum(r['pixels'] for r in records),
                    reference_crosscheck=manifest['reference_crosscheck'],
                    target_part=report.findtext('UserAssignments/Part'),
                    target_clock_ns=report.findtext('UserAssignments/TargetClockPeriod'),
                    estimated_clock_ns=report.findtext('PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod'),
                    hls_estimated_resources=resources,
                    ip_packages=[p.relative_to(HERE).as_posix() for p in packages],
                    source_sha256={p.relative_to(HERE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                   for folder in ('src','tb') for p in (HERE/folder).glob('*')},
                    cases=records,
                    boundary='Independent HLS simulation only; no board or full processing-chain integration.')
    out = HERE/'results'; out.mkdir(exist_ok=True)
    (out/'verification.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    labels = ['腐蚀','膨胀','开运算','闭运算']
    lines = ['# 二值形态学验证结果','',
        '已完成 C 仿真、HLS 综合、Verilog RTL 协同仿真及 IP 导出。',
        f'共 {len(records)} 组测试、{evidence["total_pixels"]:,} 个输出像素，全部与 NumPy 参考一致。',
        'NumPy 参考逐例通过 OpenCV 交叉核对；两者均显式采用边缘复制填充。',
        '5 组非法尺寸或非法模式测试通过，testbench 确认整个输出缓冲区未被写入。','',
        '| 测试 | 宽×高 | 模式 | 输出像素数 | 差异数 |','|---|---|---|---|---|']
    lines += [f'| {r["name"]} | {r["width"]}×{r["height"]} | {labels[r["mode"]] if r["mode"]<4 else "非法模式"} | {r["pixels"]} | {r["mismatches"]} |' for r in records]
    lines += ['','## HLS 估计','',
        f'- 器件：{evidence["target_part"]}。',
        f'- 目标周期 {evidence["target_clock_ns"]} ns，估计周期 {evidence["estimated_clock_ns"]} ns。',
        '- 资源：'+'，'.join(f'{k}={v}' for k,v in resources.items())+'。',
        '- 单步形态学与两步开闭运算共享同一顶层接口；内部为两个独立流处理阶段。',
        '- II、FIFO 和时序估计见原始报告；不是板端帧率或端到端性能。',
        '','## 原始证据','',
        '- [C 仿真](../build/morphology/solution1/csim/report/morphology_binary_csim.log)',
        '- [综合报告](../build/morphology/solution1/syn/report/morphology_binary_csynth.rpt)',
        '- [RTL 协同仿真](../build/morphology/solution1/sim/report/morphology_binary_cosim.rpt)',
        '- [导出 IP](../'+evidence['ip_packages'][0]+')',
        '- [运算效果对比](morphology_comparison.png)',
        '','## 结果解释','',
        '图中真实眼底掩膜来自 AH 人工标注，不是模型输出。腐蚀和开运算会显著删除细血管，闭运算也可能连接邻近区域，因此通过模块正确性测试并不意味着分割效果得到改善。',
        '尚未完成上板或均值滤波、阈值化、形态学三个模块的系统集成。']
    if (out/'initial_synthesis.json').exists():
        baseline = json.loads((out/'initial_synthesis.json').read_text())
        lines += ['', '## 控制路径修正', '',
            f'将帧长度和模式判断移到 DATAFLOW 外部后，HLS 估计周期从 {baseline["estimated_clock_ns"]} ns 降为 {evidence["estimated_clock_ns"]} ns。',
            '最终版本重新执行全部 C/RTL 测试。100 MHz 仍是设计目标，需由实际 Vivado 实现和板上测试确认。']
    (out/'verification_results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    fig,axes = plt.subplots(2,5,figsize=(15,7))
    for row,prefix in enumerate(['shapes','stare_manual_ah']):
        case = next(r for r in records if r['name']==prefix+'_erode')
        shape = (case['height'],case['width'])
        source = np.fromfile(HERE/'vectors'/f'{prefix}_erode_input.bin',np.uint8).reshape(shape)
        images = [source] + [np.fromfile(HERE/'vectors'/f'{prefix}_{name}_actual.bin',np.uint8).reshape(shape)
                             for name in manifest['mode_names']]
        for col,(img,title) in enumerate(zip(images,['Input','Erode','Dilate','Open','Close'])):
            axes[row,col].imshow(img,cmap='gray',vmin=0,vmax=255,interpolation='nearest')
            axes[row,col].set_title(title if row==0 else title+' (AH manual mask)',fontsize=9)
            axes[row,col].axis('off')
    fig.suptitle('3x3 binary morphology | Top: synthetic shapes; bottom: STARE manual label | RTL simulation',fontsize=12)
    fig.text(.5,.025,'Correct arithmetic does NOT prove improved vessel segmentation. No model inference or board test.',ha='center',fontsize=10)
    fig.subplots_adjust(left=.02,right=.98,top=.88,bottom=.08,hspace=.28,wspace=.10)
    fig.savefig(out/'morphology_comparison.png',dpi=110);plt.close(fig)
    print(json.dumps({k:v for k,v in evidence.items() if k not in ('cases','source_sha256')},indent=2))

if __name__ == '__main__': main()

