"""Verify saved results against independent vectors and actual HLS reports."""
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
SOLUTION = HERE / 'build' / 'threshold' / 'solution1'

def main():
    if not (HERE / 'build' / 'completed_all.txt').is_file():
        raise RuntimeError('No completed full HLS run')
    manifest = json.loads((HERE / 'vectors' / 'manifest.json').read_text())
    records = []
    for case in manifest['cases']:
        name = case['name']
        expected = np.fromfile(HERE / 'vectors' / f'{name}_expected.bin', np.uint8)
        actual = np.fromfile(HERE / 'vectors' / f'{name}_actual.bin', np.uint8)
        if expected.size != case['pixels'] or actual.size != case['pixels']:
            raise RuntimeError('Output length mismatch: ' + name)
        error = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
        records.append({**case, 'mismatches': int(np.count_nonzero(error)),
                        'max_error': int(error.max()) if error.size else 0})
    if any(r['mismatches'] for r in records):
        raise RuntimeError('Pixel mismatch')
    csim = (SOLUTION / 'csim' / 'report' / 'threshold_binary_csim.log').read_text(errors='replace')
    cosim = (SOLUTION / 'sim' / 'report' / 'threshold_binary_cosim.rpt').read_text(errors='replace')
    if f'SUMMARY cases={len(records)} mismatches=0' not in csim:
        raise RuntimeError('Missing successful C simulation')
    if not re.search(r'Verilog\s*\|\s*Pass', cosim, re.I):
        raise RuntimeError('Missing successful RTL co-simulation')
    report = ET.parse(SOLUTION / 'syn' / 'report' / 'threshold_binary_csynth.xml').getroot()
    resources = {c.tag: int(c.text) for c in report.find('AreaEstimates/Resources')}
    packages = list((SOLUTION / 'impl' / 'ip').glob('*.zip'))
    if not packages: raise RuntimeError('IP archive missing')
    evidence = {
        'csim': 'PASS', 'cosim': 'PASS', 'case_count': len(records),
        'total_pixels': sum(r['pixels'] for r in records),
        'target_part': report.findtext('UserAssignments/Part'),
        'target_clock_ns': report.findtext('UserAssignments/TargetClockPeriod'),
        'estimated_clock_ns': report.findtext('PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod'),
        'hls_estimated_resources': resources, 'cases': records,
        'ip_packages': [p.relative_to(HERE).as_posix() for p in packages],
        'source_sha256': {p.relative_to(HERE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for folder in ('src', 'tb') for p in (HERE / folder).glob('*')},
        'boundary': 'Standalone HLS C/RTL verification only, no board testing or integrated two-IP test.'
    }
    out = HERE / 'results'; out.mkdir(exist_ok=True)
    (out / 'verification.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    lines = ['# 可调阈值二值化验证结果', '',
        'C 仿真通过、HLS 综合成功、Verilog RTL 协同仿真通过、Vivado IP 导出成功。',
        f'共 {len(records)} 组测试、{evidence["total_pixels"]:,} 个有效输出像素，逐像素差异为 0。',
        '包含 4 组非法尺寸测试，由 testbench 核对整个输出缓冲区仍为哨兵值。',
        '', '| 测试 | 宽×高 | 阈值 | 输出像素数 | 差异数 |',
        '|---|---|---|---|---|']
    lines += [f'| {r["name"]} | {r["width"]}×{r["height"]} | {r["threshold"]} | {r["pixels"]} | {r["mismatches"]} |' for r in records]
    lines += ['', '## HLS 估计', '',
        f'- 器件：{evidence["target_part"]}。',
        f'- 目标时钟：{evidence["target_clock_ns"]} ns；估计时钟：{evidence["estimated_clock_ns"]} ns。',
        '- 资源：' + '，'.join(f'{k}={v}' for k,v in resources.items()) + '。',
        '- 详细循环 II、AXI 接口等见综合报告；估计不是实现后或开发板实测值。',
        '', '## 原始证据', '',
        '- [C 仿真日志](../build/threshold/solution1/csim/report/threshold_binary_csim.log)',
        '- [综合报告](../build/threshold/solution1/syn/report/threshold_binary_csynth.rpt)',
        '- [RTL 协同仿真报告](../build/threshold/solution1/sim/report/threshold_binary_cosim.rpt)',
        '- [导出 IP](../' + evidence['ip_packages'][0] + ')',
        '', '## 验证范围', '',
        '规则为输入大于等于阈值输出 255，否则输出 0。等于阈值、阈值极值、连续调用修改阈值以及非法尺寸均有覆盖。',
        '滤波后输入来自独立 NumPy 参考，不表示两个硬件 IP 已完成联合验证。未上板。',
        '眼底图简单阈值化只是模块演示，不是模型推理或有效血管分割证明。']
    (out / 'verification_results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    fig, axes = plt.subplots(2, 3, figsize=(13, 8))
    for row, name in enumerate(['stare_green_t128', 'stare_mean_t128']):
        case = next(r for r in records if r['name'] == name)
        shape = (case['height'], case['width'])
        inp = np.fromfile(HERE / 'vectors' / f'{name}_input.bin', np.uint8).reshape(shape)
        actual = np.fromfile(HERE / 'vectors' / f'{name}_actual.bin', np.uint8).reshape(shape)
        expected = np.fromfile(HERE / 'vectors' / f'{name}_expected.bin', np.uint8).reshape(shape)
        error = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
        images = [(inp, 'Green input' if row == 0 else 'NumPy mean-filtered input'),
                  (actual, 'RTL threshold output: pixel >= 128'), (error, 'Absolute error vs NumPy: 0')]
        for col, (image, title) in enumerate(images):
            axes[row,col].imshow(image, cmap='gray' if col < 2 else 'magma',
                                vmin=0, vmax=255 if col < 2 else 1, interpolation='nearest')
            axes[row,col].set_title(title, fontsize=10); axes[row,col].axis('off')
    fig.suptitle('Threshold module simulation | NOT vessel segmentation or model prediction', fontsize=13)
    fig.subplots_adjust(left=0.025, right=0.975, top=0.90, bottom=0.035, hspace=0.23, wspace=0.06)
    fig.savefig(out / 'threshold_comparison.png', dpi=100); plt.close(fig)
    print(json.dumps({k:v for k,v in evidence.items() if k not in ('cases','source_sha256')},indent=2))
if __name__ == '__main__':
    main()

