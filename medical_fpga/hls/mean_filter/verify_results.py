"""Verify saved HLS outputs and summarize tool evidence; does not run hardware."""
from pathlib import Path
import json
import re
import hashlib
import xml.etree.ElementTree as ET
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
SOLUTION = HERE / 'build' / 'mean_filter' / 'solution1'

def main():
    manifest = json.loads((HERE / 'vectors' / 'manifest.json').read_text())
    checks = []
    for case in manifest['cases']:
        name = case['name']
        expected = np.fromfile(HERE / 'vectors' / f'{name}_expected.bin', dtype=np.uint8)
        actual = np.fromfile(HERE / 'vectors' / f'{name}_actual.bin', dtype=np.uint8)
        if actual.size != case['pixels'] or expected.size != case['pixels']:
            raise RuntimeError('Output length mismatch: ' + name)
        error = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
        checks.append({**case, 'mismatches': int(np.count_nonzero(error)), 'max_error': int(error.max())})
    if any(c['mismatches'] for c in checks):
        raise RuntimeError('Pixel comparison failed')
    csim = (SOLUTION / 'csim' / 'report' / 'mean_filter_csim.log').read_text(errors='replace')
    cosim_path = SOLUTION / 'sim' / 'report' / 'mean_filter_cosim.rpt'
    cosim = cosim_path.read_text(errors='replace')
    if 'SUMMARY cases=12 mismatches=0' not in csim or not re.search(r'Verilog\s*\|\s*Pass', cosim, re.I):
        raise RuntimeError('Missing successful C/RTL tool evidence')
    report = ET.parse(SOLUTION / 'syn' / 'report' / 'mean_filter_csynth.xml').getroot()
    resources = {child.tag: int(child.text) for child in report.find('AreaEstimates/Resources')}
    packages = list((SOLUTION / 'impl' / 'ip').glob('*.zip'))
    if not packages:
        raise RuntimeError('Exported IP archive missing')
    evidence = {
        'csim': 'PASS', 'cosim': 'PASS', 'cases': checks,
        'total_pixels': sum(c['pixels'] for c in checks),
        'target_part': report.findtext('UserAssignments/Part'),
        'target_clock_ns': report.findtext('UserAssignments/TargetClockPeriod'),
        'estimated_clock_ns': report.findtext('PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod'),
        'hls_estimated_resources': resources,
        'ip_packages': [p.relative_to(HERE).as_posix() for p in packages],
        'source_sha256': {p.relative_to(HERE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (HERE / 'src').glob('*')},
        'boundary': 'HLS C/RTL simulation and IP export only; no board testing or system implementation.'
    }
    out = HERE / 'results'
    out.mkdir(exist_ok=True)
    (out / 'verification.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    lines = [
        '# 均值滤波 HLS 验证结果', '',
        '已通过 C 仿真、HLS 综合和 Verilog RTL 协同仿真，并导出 IP。',
        '尚未进行完整 Vivado 系统实现或开发板测试。', '',
        f'共 {len(checks)} 组测试、{evidence["total_pixels"]:,} 个输出像素，逐像素误差均为 0。',
        '参考定义：8 位灰度、边缘复制、3×3 求和后整数除以 9 向下取整。', '',
        '| 测试 | 宽×高 | 不同像素数 | 最大误差 |',
        '|---|---|---|---|'
    ]
    lines += [f'| {c["name"]} | {c["width"]}×{c["height"]} | {c["mismatches"]} | {c["max_error"]} |' for c in checks]
    lines += ['', '## HLS 估计结果', '',
        f'- 目标器件：{evidence["target_part"]}。',
        f'- 目标时钟周期：{evidence["target_clock_ns"]} ns；HLS 估计周期：{evidence["estimated_clock_ns"]} ns。',
        '- 资源估计：' + '，'.join(f'{k}={v}' for k, v in resources.items()) + '。',
        '- 像素循环 II=1；整帧仍有行切换、填充与 AXI 访问开销，不能据此声称板上帧率。',
        '- HLS 报告存在行循环不能展平、行缓存依赖声明及 AXI 同步低有效复位提示；当前未启用 rewind 优化。边界及连续多帧测试通过，实际系统仍需检查接口和时序。',
        '', '## 查看证据', '',
        '- C 仿真：../build/mean_filter/solution1/csim/report/mean_filter_csim.log',
        '- 综合：../build/mean_filter/solution1/syn/report/mean_filter_csynth.rpt',
        '- RTL 协同仿真：../build/mean_filter/solution1/sim/report/mean_filter_cosim.rpt',
        '- IP：' + ', '.join('../' + p for p in evidence['ip_packages']),
        '- 详细数据和源码哈希：verification.json',
        '', '滤波效果图只展示数值运算，不证明它可以提高血管分割精度。']
    (out / 'verification_results.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    case = next(c for c in checks if c['name'] == 'stare_im0001_green')
    shape = (case['height'], case['width'])
    original = np.fromfile(HERE / 'vectors' / 'stare_im0001_green_input.bin', np.uint8).reshape(shape)
    actual = np.fromfile(HERE / 'vectors' / 'stare_im0001_green_actual.bin', np.uint8).reshape(shape)
    expected = np.fromfile(HERE / 'vectors' / 'stare_im0001_green_expected.bin', np.uint8).reshape(shape)
    fig, axes = plt.subplots(2, 3, figsize=(13, 8))
    crop = (slice(180, 340), slice(250, 410))
    for row, region in enumerate([np.s_[:, :], crop]):
        data = [original[region], actual[region], np.abs(actual[region].astype(int) - expected[region].astype(int))]
        for col, (arr, title) in enumerate(zip(data, ['Input green channel', 'HLS RTL output: 3x3 mean', 'Absolute error vs NumPy: 0'])):
            axes[row, col].imshow(arr, cmap='gray' if col < 2 else 'magma', vmin=0, vmax=255 if col < 2 else 1, interpolation='nearest')
            axes[row, col].set_title(title + (' (crop)' if row else ''), fontsize=10)
            axes[row, col].axis('off')
    fig.suptitle('STARE im0001 | Full image and matching detail | Simulation output, not board output', fontsize=12)
    fig.tight_layout()
    fig.savefig(out / 'filter_comparison.png', dpi=100)
    plt.close(fig)
    print(json.dumps({k: v for k, v in evidence.items() if k != 'cases'}, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()

