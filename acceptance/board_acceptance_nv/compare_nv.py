"""f150 -> f150nv before / after numbers (2026-11-03). Reads only files already written by acceptance.py / regression.py /
run_board.py plus the Vivado reports; writes performance_comparison_f150nv.md + compare_nv.json next to this file.

  before = f150 + v5.26 (baseline of this acceptance; tile / live runs: board_acceptance_v524/tile.json and
           board_acceptance_v526/results_post_activate_9851, both f150 with vis_layer)
  after  = f150nv + v5.26 (this acceptance)

    python compare_nv.py
"""
import json
import re
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLD = HERE.parent
VIV = PLD / 'tcp_image/tcp_image_rx/vivado'
LIVE_BEFORE = PLD / 'board_acceptance_v526/results_post_activate_9851'
TILE_BEFORE = PLD / 'board_acceptance_v524/tile.json'


def load(p):
    p = Path(p)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q / 100 * (len(v) - 1))))]


def util(tag):
    text = (VIV / f'ai_sys_{tag}_util_hier.rpt').read_text(encoding='utf-8', errors='replace')
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) == 11 and cells[2].isdigit() and cells[0] != 'system_wrapper':
            rows[cells[0]] = dict(lut=int(cells[2]), ff=int(cells[6]), b36=int(cells[7]), b18=int(cells[8]), dsp=int(cells[10]))
    return rows


def direct(tag):
    s = load(HERE / f'{tag}_performance.json') or []
    out = {}
    for key in sorted({(r['model'], r['res']) for r in s}):
        v = [r for r in s if (r['model'], r['res']) == key]
        v = v[1:] or v                       # first sample is cold
        out['m%d_%d' % key] = {k: round(st.mean(r[k] for r in v) / 1000, 2) for k in ('ap_us', 'put_us', 'get_us', 'run_us')}
        out['m%d_%d' % key]['n'] = len(v)
        out['m%d_%d' % key]['run_sd'] = round(st.pstdev([r['run_us'] for r in v]) / 1000, 2)
    return out


def tcp(tag):
    t = load(HERE / f'{tag}_tcp_performance.json')
    if not t: return {}
    out = {}
    for name in sorted({r['name'] for r in t['samples']}):
        v = [r for r in t['samples'] if r['name'] == name]
        out[name] = dict(ai_ms=round(st.mean(r['ai_ms'] for r in v), 2), total_ms=round(st.mean(r['total_ms'] for r in v), 2),
                         total_p99=round(pct([r['total_ms'] for r in v], 99), 2), n=len(v))
    return out


def pp(tag):
    p = HERE / f'{tag}_pp_timing.txt'
    if not p.exists(): return {}
    pl = [float(m.group(1)) for m in re.finditer(r'pl ([\d.]+) ms', p.read_text(encoding='utf-8'))]
    return dict(cases=len(pl), pl_ms_mean=round(st.mean(pl), 3), pl_ms_sum=round(sum(pl), 1))


def boardcheck(tag):
    p = HERE / f'{tag}_boardcheck_report.md'
    if not p.exists(): return {}
    out = {}
    for m in re.finditer(r'^\| (PASS|FAIL|WARN|SKIP) \| ([^|]+?) \| ([\d.]+) \|', p.read_text(encoding='utf-8'), re.M):
        out[m.group(2)] = float(m.group(3))
    m = re.search(r'ap\(PL计算\)=(\d+) ms \| put\(PS->BRAM\)=(\d+) \| get\(BRAM->PS\)=(\d+)', p.read_text(encoding='utf-8'))
    if m: out['512 ap/put/get'] = '/'.join(m.groups())
    return out


def profile(tag):
    p = load(HERE / f'{tag}_node_profile.json')
    return {k: round(sum(v.values()) / 1000, 2) for k, v in p.items()} if p else {}


def live(directory):
    out = {}
    for p in sorted(Path(directory).glob('*.json')):
        if p.name in ('caps.json', 'host_check.json'): continue
        r = (load(p) or {}).get('result') or {}
        s = r.get('stream') or {}
        k = r.get('keyframe') or {}
        sp = r.get('pl_split') or {}
        out[p.stem] = dict(frames=r.get('n_frames'), fps=r.get('fps'), lat=s.get('latency_ms'), pl_ms=r.get('pl_ms'),
                           ap_ms=sp.get('ap_ms'), put_ms=sp.get('put_ms'), get_ms=sp.get('get_ms'),
                           pl_ms_per_key=k.get('pl_ms_per_key'), n_key=k.get('n_key'),
                           track_ms=k.get('track_ms_per_frame'), ef=r.get('ef'))
    return out


def tile(path):
    t = load(path)
    if not t: return {}
    out = {}
    for it in (t.get('C_large') or {}).get('items', []):
        u = it.get('upload') or {}
        out[it['level']] = dict(tiles=u.get('tiles'), job_ms=u.get('job_ms'), unet_ms=u.get('unet_ms'), ovl_ms=u.get('ovl_ms'),
                                vis_pl_ms=u.get('vis_pl_ms'), pp_pl_ms=u.get('pp_pl_ms'))
    return out


def d(a, b, fmt='%.2f'):
    if a in (None, '') or b in (None, ''): return '%s | %s | -' % (a, b)
    try:
        return (fmt + ' | ' + fmt + ' | %+.1f%%') % (a, b, (b - a) / a * 100 if a else 0)
    except TypeError:
        return '%s | %s | -' % (a, b)


def main():
    res = dict(util={t: util(t) for t in ('f150', 'f150nv')}, direct={t: direct(t) for t in ('before', 'after')},
               tcp={t: tcp(t) for t in ('before', 'after')}, pp={t: pp(t) for t in ('before', 'after')},
               boardcheck={t: boardcheck(t) for t in ('before', 'after')}, profile={t: profile(t) for t in ('before', 'after')},
               live=dict(before=live(LIVE_BEFORE), after=live(HERE / 'results')),
               tile=dict(before=tile(TILE_BEFORE), after=tile(HERE / 'tile.json')),
               regression=load(HERE / 'summary.json'), host_check=load(HERE / 'results/host_check.json'),
               state=load(HERE / 'state_f150nv.json'))
    (HERE / 'compare_nv.json').write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding='utf-8')

    L = ['# f150 → f150nv（去掉 vis_layer）前后对比', '',
         '前 = f150 + 服务端 v5.26；后 = f150nv + v5.26（同一服务端二进制）。计时均为板上实测均值（首个冷启动样本剔除）。', '']
    u0, u1 = res['util']['f150'], res['util']['f150nv']
    L += ['## 1. PL 资源（布线后）', '', '| 模块 | LUT 前 | LUT 后 | BRAM36 前 | 后 | BRAM18 前 | 后 | DSP 前 | 后 |', '|---|---|---|---|---|---|---|---|---|']
    for k in u0:
        a, b = u0[k], u1.get(k, dict(lut=0, ff=0, b36=0, b18=0, dsp=0))
        L.append('| %s | %d | %d | %d | %d | %d | %d | %d | %d |' % (k, a['lut'], b['lut'], a['b36'], b['b36'], a['b18'], b['b18'], a['dsp'], b['dsp']))
    L += ['', '时序（按板上实际 pl0_ref 101.376 MHz → 152.15 MHz 复核）：f150 WNS +0.470 / WHS +0.013；f150nv WNS +0.486 / WHS +0.015。', '']

    L += ['## 2. U-Net 直接调用（strict_net_test，逐位比对通过）', '', '| 模型_分辨率 | 指标 | 前 (ms) | 后 (ms) | 变化 |', '|---|---|---|---|---|']
    for key, a in res['direct']['before'].items():
        b = res['direct']['after'].get(key, {})
        for k in ('ap_us', 'put_us', 'get_us', 'run_us'):
            L.append('| %s | %s | %s |' % (key, k.replace('_us', ''), d(a.get(k), b.get(k))))
    L += ['', '逐层 ap 合计（ms）：' + '；'.join('%s 前 %s / 后 %s' % (k, v, res['profile']['after'].get(k)) for k, v in res['profile']['before'].items()), '']

    L += ['## 3. TCP 往返（PC → 板 → PC）', '', '| 用例 | 指标 | 前 | 后 | 变化 |', '|---|---|---|---|---|']
    for name, a in res['tcp']['before'].items():
        b = res['tcp']['after'].get(name, {})
        for k in ('ai_ms', 'total_ms', 'total_p99'):
            L.append('| %s | %s | %s |' % (name, k, d(a.get(k), b.get(k))))
    L += ['']

    a, b = res['pp']['before'], res['pp']['after']
    L += ['## 4. PL 后处理 pp_layer（182 例向量，逐位一致）', '', '| 指标 | 前 | 后 | 变化 |', '|---|---|---|---|',
          '| 平均每例 PL 时间 (ms) | %s |' % d(a.get('pl_ms_mean'), b.get('pl_ms_mean'), '%.3f'), '']

    L += ['## 5. BoardCheck 计时项（ms）', '', '| 项 | 前 | 后 | 变化 |', '|---|---|---|---|']
    for k, v in res['boardcheck']['before'].items():
        w = res['boardcheck']['after'].get(k)
        L.append('| %s | %s |' % (k, d(v, w) if isinstance(v, float) else '%s | %s | -' % (v, w)))
    L += ['']

    L += ['## 6. 心脏现场模式（v5.26 run_board.py，50 fps 信号源）', '',
          '前 = board_acceptance_v526/results_post_activate_9851（f150，11-03 上午）。延迟定义：采到 → 掩膜 + 容积就绪。', '',
          '| 用例 | 指标 | 前 | 后 | 变化 |', '|---|---|---|---|---|']
    for case, a in res['live']['before'].items():
        b = res['live']['after'].get(case, {})
        la, lb = a.get('lat') or {}, b.get('lat') or {}
        for k in ('p50', 'p99', 'max'):
            L.append('| %s | 延迟 %s (ms) | %s |' % (case, k, d(la.get(k), lb.get(k))))
        for k in ('pl_ms_per_key', 'track_ms', 'ap_ms', 'put_ms', 'get_ms'):
            if a.get(k) is not None: L.append('| %s | %s | %s |' % (case, k, d(a.get(k), b.get(k))))
        L.append('| %s | EF | %s | %s | %s |' % (case, a.get('ef'), b.get('ef'), '一致' if a.get('ef') == b.get('ef') else '不同'))
    L += ['']

    L += ['## 7. 眼底大图分块（JOB_TILE，叠加从 PL vis_layer 改走 PS）', '',
          '前 = board_acceptance_v524/tile.json（f150 + v5.24，叠加在 PL）；后 = 本次（叠加在 PS）。', '',
          '| 图 | 指标 | 前 | 后 | 变化 |', '|---|---|---|---|---|']
    for lv, a in res['tile']['before'].items():
        b = res['tile']['after'].get(lv, {})
        for k in ('job_ms', 'unet_ms', 'ovl_ms', 'vis_pl_ms', 'pp_pl_ms'):
            L.append('| %s | %s | %s |' % (lv, k, d(a.get(k), b.get(k))))
    L += ['']

    reg = res['regression'] or {}
    hc = res['host_check'] or {}
    L += ['## 8. 回归', '', '回归总判定：%s；' % reg.get('pass') +
          '组：' + '，'.join('%s %s' % (k, 'PASS' if v.get('passed') else 'FAIL') for k, v in (reg.get('tests') or {}).items()), '',
          '心脏现场 host_check：%s' % hc.get('pass_'), '']
    (HERE / 'performance_comparison_f150nv.md').write_text('\n'.join(L), encoding='utf-8')
    print('COMPARE_NV_DONE')


if __name__ == '__main__':
    main()
