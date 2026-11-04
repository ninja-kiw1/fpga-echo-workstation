"""F 提频：对比原 IP（10 ns）与新周期的 HLS 综合报告（csynth.rpt 顶层表）。
用法：python cmp_csynth.py p6757   → 每个核打印：估计时钟、资源，以及 II / 迭代延迟有变化的循环。"""
import re, sys, pathlib

B = pathlib.Path(__file__).resolve().parent.parent / 'build'
PAIRS = [('conv', 'conv_ip_xu2'), ('elt', 'elt_ip_e2b'), ('pp', 'pp_ip'), ('vis', 'vis_ip')]


def table(prj):
    t = (B / prj / 'solution1/syn/report/csynth.rpt').read_text(errors='ignore')
    t = t[t.index('Performance & Resource Estimates'):]
    t = t[:t.index('HW Interfaces')]
    rows = []
    for ln in t.splitlines():
        if not ln.startswith('|') or 'Modules' in ln or '& Loops' in ln:
            continue
        c = [x.strip() for x in ln.strip('|').split('|')]
        if len(c) < 14 or not c[0][:1] in '+o* ' or not c[0]:
            continue
        rows.append(dict(name=c[0], it=c[3], ii=c[4], trip=c[5], lat=c[7], slack=c[9],
                         bram=c[10], dsp=c[11], ff=c[12], lut=c[13]))
    est = re.search(r'Estimated.*?\|\s*([\d.]+)\s*\|', (B / prj / 'solution1/syn/report/csynth.rpt').read_text(errors='ignore'))
    return rows


def main(p):
    for core, old in PAIRS:
        new = f'f_{core}_{p}'
        a, b = table(old), table(new)
        print(f'==== {core}: {old} -> {new}')
        top_a, top_b = a[0], b[0]
        for k in ('bram', 'dsp', 'ff', 'lut'):
            print(f'   {k:5s} {top_a[k]:>14s} -> {top_b[k]:>14s}')
        print(f'   top slack {top_a["slack"]} -> {top_b["slack"]}')
        da = {r['name']: r for r in a}
        n_same = 0
        for r in b:
            o = da.get(r['name'])
            if o is None:
                print(f'   NEW  {r["name"]:48s} it {r["it"]:>5s} II {r["ii"]:>4s} lat {r["lat"]}')
                continue
            if (o['it'], o['ii'], o['lat']) != (r['it'], r['ii'], r['lat']):
                print(f'   DIFF {r["name"]:48s} it {o["it"]:>5s}->{r["it"]:<5s} II {o["ii"]:>4s}->{r["ii"]:<4s} '
                      f'trip {r["trip"]:>6s} lat {o["lat"]:>9s}->{r["lat"]}')
            else:
                n_same += 1
        gone = [n for n in da if n not in {r['name'] for r in b}]
        for n in gone:
            print(f'   GONE {n}')
        print(f'   ({n_same} rows unchanged)')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'p6757')
