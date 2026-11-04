"""Host check of the P1 1.6 extension of src/job_simpson.c against tcp_image_ui/cardiac_measure.measure (bit for bit).

VAL 50 patients (test split not touched), two inputs each:
  expert  CAMUS expert masks at the original size, no resize, no clean (labels 1 / 1|2 / 3)
  pred    the board-equivalent integer-model outputs of echo/results/quant/{lv,epi,la}_r128/outputs (128 -> x4 -> 512
          upload, the job resizes back with Pillow NEAREST and cleans) = echo/eval_p1_16.py
Compared: la_truncated, la_raw_ml, la_ml (calibrated), endo_ml, epi_ml, lv_mass_g, wall_mm, wall_mm_views, and
EDV / ESV / EF unchanged. Python errors (no mitral plane) are counted; the C job then reports nulls instead.
Part A (10-16): also lvidd_mm, lvidd_mm_views, rwt (value only), and with the patient params bsa_m2, lvmi and
lvmi_class (code + limit). CAMUS has the sex but no height / weight, so each request carries a synthetic height / weight
(PATIENT(), deterministic, spread to hit both classes); every 10th patient sends no patient params (old 32-byte
request: no bsa / class), the one before it no sex (lvmi_class null).
Also --board: the same requests to the board server (v5.16) and the same comparison, plus the installed program
packages camus_la (17) / camus_epi (18) on all 200 VAL frames (cache img512[::4, ::4], grey): board int8 logits ==
results/quant/<tag>/outputs/val_*.npy (the integer reference eval_p1_16 uses), bit for bit.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe compare.py [--board]      (gcc via WSL)
"""
import argparse
import json
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RX = os.path.normpath(os.path.join(HERE, '..', '..'))
UI = os.path.normpath(os.path.join(RX, '..', 'tcp_image_ui'))
ECHO = os.path.normpath(os.path.join(RX, '..', '..', 'echo'))
sys.path.insert(0, UI)
sys.path.insert(0, ECHO)
import cardiac_measure as cm  # noqa: E402
import ef_simpson as ef  # noqa: E402
import jobs  # noqa: E402
from common import CACHE, CAMUS, LABELS, RESULTS  # noqa: E402
from eval_p1_16 import TAGS, to_original  # noqa: E402
from nifti import read_nifti  # noqa: E402

WORK = os.path.join(HERE, 'work')
KEYS = ('la_raw_ml', 'la_ml', 'endo_ml', 'epi_ml', 'lv_mass_g', 'wall_mm', 'lvidd_mm', 'rwt', 'bsa_m2', 'lvmi')
CLS_KEYS = ('code', 'lvmi_limit')


def PATIENT(i, sex):
    """synthetic (height cm, weight kg, sex) for VAL patient number i (sorted order); None = old request."""
    if i % 10 == 9:
        return None
    return 150.0 + (i * 7) % 41, 40.0 + (i * 13) % 61 + 0.5 * (i % 2), None if i % 10 == 8 else sex


def wsl(p):
    p = os.path.abspath(p).replace('\\', '/')
    return '/mnt/' + p[0].lower() + p[2:]


def build_cases():
    meta = json.loads((CACHE / 'val_meta.json').read_text(encoding='utf-8'))
    q = {k: [np.load(RESULTS / 'quant' / t / 'outputs' / f'val_{i:03d}.npy') for i in range(len(meta))] for k, t in TAGS.items()}
    pats = {}
    for i, m in enumerate(meta):
        p, view, phase = m['patient'], m['view'], m['phase']
        gt, sp = read_nifti(CAMUS / 'database_nifti' / p / f'{p}_{view}_{phase}_gt.nii.gz')
        d = pats.setdefault(p, {})
        d[(view, 'spacing')] = (sp[1], sp[0])
        d[(view, 'size')] = (m['width'], m['height'])
        d['sex'] = m.get('sex') or read_sex(p)
        ref = {'endo': gt == LABELS['lv'], 'epi': np.isin(gt, (LABELS['lv'], LABELS['myo'])), 'la': gt == LABELS['la']}
        for k in TAGS:
            d[('gt', k, view, phase)] = ref[k]
            d[('q', k, view, phase)] = np.repeat(np.repeat(q[k][i] >= 1, 4, 0), 4, 1)          # the 512 upload
            d[('pred', k, view, phase)] = to_original(q[k][i], m['width'], m['height'])
    return pats


def read_sex(p):
    for line in (CAMUS / 'database_nifti' / p / 'Info_2CH.cfg').read_text().splitlines():
        if line.startswith('Sex:'):
            return line.split(':')[1].strip()
    return None


ORDER = [('endo', '2CH', 'ED'), ('endo', '2CH', 'ES'), ('endo', '4CH', 'ED'), ('endo', '4CH', 'ES'),
         ('epi', '2CH', 'ED'), ('epi', '4CH', 'ED'), ('la', '2CH', 'ES'), ('la', '4CH', 'ES')]


def request(d, src, pt):
    if src == 'gt':
        masks = [d[('gt',) + o] for o in ORDER]
        return jobs.simpson_request(masks, d[('2CH', 'spacing')], d[('4CH', 'spacing')], patient=pt)
    masks = [d[('q',) + o] for o in ORDER]
    outs = [d[(o[1], 'size')] for o in ORDER]
    return jobs.simpson_request(masks, d[('2CH', 'spacing')], d[('4CH', 'spacing')], out_sizes=outs, clean=True,
                                patient=pt)


def python_ref(d, src, pt):
    m = 'gt' if src == 'gt' else 'pred'
    vv = {view: dict(spacing=d[(view, 'spacing')], ed_endo=d[(m, 'endo', view, 'ED')], ed_epi=d[(m, 'epi', view, 'ED')],
                     es_la=d[(m, 'la', view, 'ES')]) for view in ('2CH', '4CH')}
    try:
        r = cm.measure(vv, *(pt or (None, None, None)), calibrate=True)
    except Exception as e:
        return dict(error=repr(e))
    edv, esv = ef.volumes(d[(m, 'endo', '2CH', 'ED')], d[(m, 'endo', '2CH', 'ES')], d[('2CH', 'spacing')],
                          d[(m, 'endo', '4CH', 'ED')], d[(m, 'endo', '4CH', 'ES')], d[('4CH', 'spacing')])
    r.update(edv=edv, esv=esv)
    return r


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return float(a) == float(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--board', action='store_true')
    a = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    pats = build_cases()
    files, refs = [], []
    for i, p in enumerate(sorted(pats)):
        pt = PATIENT(i, pats[p]['sex'])
        for src in ('gt', 'pred'):
            params, data = request(pats[p], src, pt)
            f = os.path.join(WORK, f'{p}_{src}.bin')
            with open(f, 'wb') as fh:
                fh.write(struct.pack('<II', len(params), len(data)) + params + data)
            files.append(f)
            refs.append((p, src, python_ref(pats[p], src, pt)))
    prog = None
    if a.board:
        import imagelink
        import proto
        link = imagelink.ImageLink('192.168.1.254', 5060, timeout=60)
        x = np.load(CACHE / 'val_img512.npy', mmap_mode='r')
        prog = {}
        for key, pid in (('la', 17), ('epi', 18)):
            diff = 0
            link.connect()
            for i in range(x.shape[0]):
                g = np.ascontiguousarray(x[i][::4, ::4]).tobytes()
                st, lg, _, _ = link.v6c_round_trip(g, 128, model0=pid, thr=1, flags=proto.FLAG_STATS | proto.FLAG_C_LOGIT)
                ref = np.load(RESULTS / 'quant' / TAGS[key] / 'outputs' / f'val_{i:03d}.npy').astype(np.int8)
                diff += int((np.frombuffer(lg, np.int8).reshape(128, 128) != ref).sum())
            prog[key] = dict(id=pid, frames=int(x.shape[0]), logit_diff=diff)
            print('program', key, prog[key], flush=True)
        out = []
        for f in files:
            raw = open(f, 'rb').read()
            pb, db = struct.unpack('<II', raw[:8])
            link.connect()
            out.append(jobs.run(link, jobs.JOB_SIMPSON_EF, 0, raw[8:8 + pb], raw[8 + pb:])['result'])
    else:
        exe = os.path.join(WORK, 'harness_card')
        cmd = (f"cd {wsl(HERE)} && gcc -std=gnu99 -O2 -Wall -Wextra -I../../src harness_card.c -lm -o work/harness_card && "
               f"./work/harness_card " + ' '.join('work/' + os.path.basename(f) for f in files))
        res = subprocess.run(['wsl', '-e', 'bash', '-c', cmd], capture_output=True, text=True,
                             env=dict(os.environ, MSYS_NO_PATHCONV='1'))
        if res.returncode:
            raise SystemExit(res.stderr)
        out = [json.loads(line) for line in res.stdout.splitlines() if line.startswith('{')]
        assert len(out) == len(files), (len(out), res.stderr[-2000:])
        del exe
    rows, bad, py_err = [], 0, 0
    for (p, src, r), c in zip(refs, out):
        row = dict(patient=p, src=src)
        if 'error' in r:
            py_err += 1
            row.update(python_error=r['error'], c=c.get('cardiac'), c_ok=c.get('ok'))
            rows.append(row)
            continue
        cc = c.get('cardiac') or {}
        diffs = []
        if not c.get('ok'):
            diffs.append('c_fail:' + str(c.get('error')))
        else:
            if cc.get('la_truncated') != r['la_truncated']:
                diffs.append('la_truncated')
            for k in KEYS:
                if not same(cc.get(k), r.get(k)):
                    diffs.append(k)
            if [None if v is None else float(v) for v in cc.get('wall_mm_views', [])] != \
                    [None if v is None else float(v) for v in r['wall_mm_views']]:
                diffs.append('wall_mm_views')
            if [float(v) for v in cc.get('lvidd_mm_views', [])] != [float(v) for v in r['lvidd_mm_views']]:
                diffs.append('lvidd_mm_views')
            if ('lvmi_class' in cc) != ('lvmi_class' in r):
                diffs.append('lvmi_class_presence')
            elif 'lvmi_class' in r:
                gp, gc = r['lvmi_class'], cc['lvmi_class']
                if (gp is None) != (gc is None) or (gp is not None and any(gp[k] != gc[k] for k in CLS_KEYS)):
                    diffs.append('lvmi_class')
            if c['edv'] != r['edv'] or c['esv'] != r['esv']:
                diffs.append('edv/esv')
        bad += bool(diffs)
        geo = lambda g: g and g['code']
        row.update(diffs=diffs, python=dict({k: r.get(k) for k in KEYS + ('la_truncated',)}, lvmi_class=geo(r.get('lvmi_class'))),
                   c=dict({k: cc.get(k) for k in KEYS + ('la_truncated',)}, lvmi_class=geo(cc.get('lvmi_class'))))
        rows.append(row)
    bad_prog = sum(v['logit_diff'] != 0 for v in (prog or {}).values())
    classes = {}
    for r in rows:
        if 'diffs' in r:
            k = str(r['c']['lvmi_class'])
            classes[k] = classes.get(k, 0) + 1
    summary = dict(where='board' if a.board else 'host', requests=len(files), compared=len(files) - py_err,
                   lvmi_classes=classes,
                   mismatches=bad, programs=prog, python_errors=py_err, la_truncated=sum(bool((r.get('c') or {}).get('la_truncated'))
                                                                          for r in rows),
                   verdict='CARDIAC_%s_PASS' % ('BOARD' if a.board else 'HOST') if bad == 0 and bad_prog == 0 else 'FAIL')
    json.dump(dict(summary=summary, rows=rows), open(os.path.join(HERE, 'cardiac_%s.json' % summary['where']), 'w'),
              indent=1, default=float)
    print(json.dumps(summary, indent=1))
    for r in rows:
        if r.get('diffs'):
            print(r['patient'], r['src'], r['diffs'], r['python'], r['c'])


if __name__ == '__main__':
    main()
