"""2026-11-02 v5.26 candidate: copy of board_check_20261102/regression.py (slim, stability 1000 frames) with SERVER v5.26.
Server v5.24 regression (copied from board_acceptance_v520, only SERVER / strain --server changed; v5.24 = HC01 whole-heart on echo_ef modes 2 / 3). Server v5.20 regression (part C: JOB_TILE exact tiled inference of a box on an image of any size + the PC box-select
zoom that replaces the old ROI UI; firmware f150 30f06745 unchanged, programs 17-20 installed, pyramid levels in /data/hrf):
the 21 groups of board_acceptance_v518 (unchanged, incl. the fundus ROI checks of verify_board_v4 / verify_board_v5_ui, which
now go through the new box-select at magnification 1) + the v5.19 strain checks (verify_board_strain.py --server v5.20,
the window path check_ui_board.py) + part D's program check (verify_board_fundus_cdr.py --eval-name v1) + part C:
tile (verify_board_tile.py --files: 1x box == v5 ROI frame == golden, tiles == integer reference, pp / vis / PS paths,
/data files, order, errors), tile_ui (verify_board_tile_ui.py: the real Tk windows), tile_plan (board planner == Python,
host only). Copied from board_acceptance_v518/regression.py (not edited there); v516 caps must report "v5.20".
Its original notes follow.
Server v5.18 regression (part A: cardiac LVIDd / RWT value / BSA / LVMI / LVMI class in simpson_ef, on top of v5.17 J1;
firmware f150 30f06745, programs 17 / 18 installed): the 20 groups of board_acceptance_f + the UI path of part A
(_verify_cardiac_ui.py with patient 170 cm / 65 kg / M). Copied from board_acceptance_f/regression.py (not edited there).
Differences from f: cardiac is STRICT again (the part A fields must match cardiac_measure bit for bit -- the PART_A_KEYS
tolerance is gone; compare.py's own verdict + logits + 0 mismatches), the v516 caps check requires server "v5.18"
(verify_board_v516.py itself hard-codes "v5.16"; all its other checks must pass), stability 5000 frames writes its own file.
Its original notes follow.
F clock-raise regression (firmware ai_sys_f<MHz> = r2 + PL MMCM, server v5.16 e11199f3 unchanged, programs 17 / 18 installed):
the 18 groups of board_acceptance_v516 + verify_board_v516 (PL registration engine / overlay / 3D render) + cardiac
compare --board (programs 17 / 18 logits, 8-mask simpson_ef) + 5000-frame mixed stability (plan F: "建议加跑 5000 帧").
Derived from board_acceptance_v516/regression.py; its original notes follow.
Server v5.16 regression (firmware r2 xu2dp_e2b_pp_r2, server v5.16 e11199f3, program packages 17 / 18 installed):
the same 17 groups as board_acceptance_r2 + F4 Simpson on the board (job_simpson.c changed: P1 1.6 extension),
run against v5.16. Copied from board_acceptance_r2/regression.py. The reports those scripts write into the repo are backed up first and restored afterwards
(the repo keeps the e2b evidence); the new copies end up in this folder.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe regression.py
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UI_PY = Path('C:/Users/21888/anaconda3/envs/srtp-vision/python.exe')
TESTS = [
    ('echo', 'echo/verify_board_echo.py', [], 'ECHO_BOARD_ACCEPTANCE_PASS', 'echo/board_check/acceptance_v5.json'),
    ('inter', 'echo/verify_board_inter.py', ['--tag', 'inter2_r128'], 'INTER_BOARD_ACCEPTANCE_PASS',
     'echo/board_check/acceptance_inter_inter2_r128.json'),
    ('f3', 'echo/verify_board_f3.py', ['--tag', 'seq_none_ex'], 'F3_BOARD_ACCEPTANCE_PASS',
     'echo/board_check/acceptance_f3_seq_none_ex.json'),
    ('reg', 'echo/verify_board_reg.py', [], 'F5_BOARD_PASS', 'echo/board_check/acceptance_f5_reg.json'),
    ('realtime', 'echo/verify_board_rt.py', [], 'RT_BOARD_PASS', 'echo/board_check/acceptance_rt_v54.json'),
    ('fundus_v4', 'tcp_image/tcp_image_ui/verify_board_v4.py', ['--output', str(HERE / 'fundus_regression.json')],
     'BOARD_V4_ACCEPTANCE_PASS', str(HERE / 'fundus_regression.json')),
    ('ui_v5', 'tcp_image/tcp_image_ui/verify_board_v5_ui.py', [], 'GUI_V5_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/gui_acceptance_v5.json'),
    ('ui_f4', 'tcp_image/tcp_image_ui/verify_board_f4_ui.py', [], 'GUI_F4_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/gui_acceptance_f4.json'),
    ('ops', 'opcomp/verify_board_ops.py', [], 'OPS_BOARD_PASS', 'opcomp/board_check/acceptance_ops_v55.json'),
    ('ct_roi', 'ct/verify_board_roi.py', ['--fine', 'roi_prior', '--coarse-model', '14', '--fine-model', '15'],
     'ROI_BOARD_PASS', 'ct/board_check/acceptance_roi_roi_prior.json'),
    ('echonet', 'echonet/verify_board_echonet.py', ['--tag', 't3', '--model', '16', '--videos', '60'],
     'ECHONET_BOARD_PASS', 'echonet/board_check/acceptance_echonet_t3.json'),
    ('echonet_ui', 'echonet/verify_ui_code_echonet.py', ['--videos', '10', '--model', '16'], 'ECHONET_UI_CODE_PASS',
     'echonet/board_check/ui_code_echonet.json'),
    ('stability_1000', 'tcp_image/tcp_image_ui/verify_board_stability_v5.py',
     ['--frames', '1000', '--out', 'stability_20261102_1000.json'], 'STABILITY_V5_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/stability_20261102_1000.json'),
    ('pp', 'tcp_image/tcp_image_ui/verify_board_pp.py', [], 'PP_BOARD_UI_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/pp_acceptance.json'),
    ('jobs', 'tcp_image/tcp_image_ui/verify_board_jobs.py', ['--quick'], 'JOBS_BOARD_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/jobs_acceptance.json'),
    ('echo_jobs', 'tcp_image/tcp_image_ui/verify_board_echo_jobs.py', ['--videos', '10', '--rt', '3'], 'ECHO_JOBS_BOARD_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/echo_jobs_acceptance.json'),
    ('clinical', 'tcp_image/tcp_image_ui/verify_board_clinical.py', ['--videos', '10', '--rt', '3'], 'CLINICAL_BOARD_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/clinical_acceptance.json'),
    ('simpson', 'tcp_image/tcp_image_ui/verify_board_simpson.py', [], 'SIMPSON_BOARD_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/simpson_acceptance.json'),
    ('v516', 'tcp_image/tcp_image_ui/verify_board_v516.py', ['--out', str(HERE / 'v516.json')], 'V516_BOARD_PASS',
     str(HERE / 'v516.json')),
    ('cardiac', 'tcp_image/tcp_image_rx/test/cardiac/compare.py', ['--board'], 'CARDIAC_BOARD_PASS',
     'tcp_image/tcp_image_rx/test/cardiac/cardiac_board.json'),
    ('cardiac_ui', 'tcp_image/tcp_image_ui/_verify_cardiac_ui.py', [], 'CARDIAC_UI_PASS', ''),
    # v5.19 (part E step 3) and part D programs
    ('strain', 'tcp_image/tcp_image_ui/verify_board_strain.py',
     ['--server', 'v5.26', '--c-kernel', '5', '--ps-clean', '5', '--out', str(HERE / 'strain.json')], 'STRAIN_BOARD_PASS',
     str(HERE / 'strain.json')),
    ('strain_ui', 'strain/step3/check_ui_board.py', ['4'], 'STRAIN_UI_BOARD_PASS', 'strain/step3/results/check_ui_board.json'),
    ('fundus_cdr', 'tcp_image/tcp_image_ui/verify_board_fundus_cdr.py', ['--eval-name', 'v1'], 'FUNDUS_CDR_BOARD_PASS',
     'tcp_image/tcp_image_ui/board_check_v5/fundus_cdr_board.json'),
    # v5.20 (part C)
    ('tile_plan', 'hrf/c2_plan_compare.py', [], 'C2_PLAN_PASS', 'hrf/results/c2_plan_compare.json'),
    ('tile', 'tcp_image/tcp_image_ui/verify_board_tile.py', ['--files', '--out', str(HERE / 'tile.json')], 'TILE_BOARD_PASS',
     str(HERE / 'tile.json')),
    ('tile_ui', 'tcp_image/tcp_image_ui/verify_board_tile_ui.py', ['--out', str(HERE / 'tile_ui.json')], 'TILE_UI_BOARD_PASS',
     str(HERE / 'tile_ui.json')),
]


# v5.18 (part A on the board):
#   v516       -- verify_board_v516.py hard-codes "v5.16" in its caps check: require server == "v5.18" with vis / render3d /
#                 overlay instead; all its other checks must pass;
#   cardiac    -- strict: compare.py's own CARDIAC_BOARD_PASS (0 mismatches over all fields incl. part A), programs 17 / 18
#                 logits identical, no Python errors, every request compared;
#   cardiac_ui -- writes no report file: rc 0 + CARDIAC_UI_PASS in its log.
SERVER = 'v5.26'


def check_v516(value):
    caps = (value.get('caps') or {}).get('caps') or {}
    caps_ok = caps.get('server') == SERVER and caps.get('vis') is True \
        and 'render3d' in caps.get('jobs', []) and 'overlay' in caps.get('jobs', [])
    others = [v.get('passed') for k, v in value.items() if isinstance(v, dict) and k != 'caps']
    return caps_ok and bool(others) and all(others)


def check_cardiac(value):
    s = value.get('summary') or {}
    progs = s.get('programs') or {}
    return s.get('verdict') == 'CARDIAC_BOARD_PASS' and s.get('mismatches') == 0 and bool(progs) \
        and all(v.get('logit_diff') == 0 for v in progs.values()) and s.get('python_errors') == 0 \
        and s.get('compared') == s.get('requests') == 100


def check_strain(value):
    s = value.get('summary') or {}
    return s.get('verdict') == 'STRAIN_BOARD_PASS' and not s.get('failed') and s.get('n', 0) >= 100


def check_verdict(marker):
    return lambda value: value.get('verdict') == marker


def check_fundus_cdr(value):
    return value.get('passed') is True and value.get('images', 0) >= 202 and not any((value.get('mismatches') or {}).values())


def check_ok(value):
    return value.get('ok') is True


CHECKS = {'v516': check_v516, 'cardiac': check_cardiac, 'strain': check_strain,
          'strain_ui': check_verdict('STRAIN_UI_BOARD_PASS'), 'fundus_cdr': check_fundus_cdr, 'tile_plan': check_ok,
          'tile': check_ok}
LOG_ONLY = {'cardiac_ui'}


def main():
    only = set(sys.argv[1:])
    backup = HERE / 'repo_reports_backup'
    backup.mkdir(exist_ok=True)
    summary = dict(utc=datetime.now(timezone.utc).isoformat(), tests={})
    # back up every repo file any test may overwrite (the whole board_check dirs, they also write images)
    dirs = ['echo/board_check', 'opcomp/board_check', 'ct/board_check', 'echonet/board_check',
            'tcp_image/tcp_image_ui/board_check_v5', 'tcp_image/tcp_image_rx/test/cardiac', 'strain/step3/results',
            'hrf/results']
    for d in dirs:
        dst = backup / d.replace('/', '__')
        if not dst.exists():
            shutil.copytree(ROOT / d, dst)
    ok = True
    try:
        for name, script, args, marker, report in TESTS:
            if only and name not in only:
                continue
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
            interp = sys.executable
            if name in ('ui_v5', 'ui_f4', 'echonet_ui', 'pp', 'echo_jobs', 'clinical', 'simpson', 'v516', 'cardiac',
                        'cardiac_ui', 'strain', 'strain_ui', 'tile', 'tile_ui'):
                interp = str(UI_PY)
                env.update(TCL_LIBRARY=str(UI_PY.parent / 'Library/lib/tcl8.6'),
                           TK_LIBRARY=str(UI_PY.parent / 'Library/lib/tk8.6'))
            log = HERE / (name + '.log')
            print('START', name, flush=True)
            with log.open('w', encoding='utf-8') as f:
                rc = subprocess.run([interp, '-u', str(ROOT / script), *args], cwd=ROOT, env=env, stdout=f,
                                    stderr=subprocess.STDOUT).returncode
            text = log.read_text(encoding='utf-8', errors='replace')
            path = Path(report) if Path(report).is_absolute() else ROOT / report
            value = json.loads(path.read_text(encoding='utf-8')) if report and path.exists() else {}
            if name in LOG_ONLY:
                passed = rc == 0 and marker in text
            elif name in CHECKS:      # narrowed checks, see above (the scripts' own verdicts are kept in summary.json)
                # v516 prints its own (hard-coded "v5.16") FAIL marker on any newer server: judged by check_v516 alone
                passed = CHECKS[name](value) and (name == 'v516' or marker in text)
            else:
                passed = rc == 0 and marker in text and bool(value.get('pass', value.get('pass_')))
            if report and path.exists() and path.parent != HERE:
                shutil.copy2(path, HERE / (name + '.json'))
            summary['tests'][name] = dict(passed=passed, rc=rc, marker=marker in text, narrowed=name in CHECKS)
            ok &= passed
            print('END', name, 'PASS' if passed else 'FAIL', flush=True)
            (HERE / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    finally:
        for d in dirs:
            src = backup / d.replace('/', '__')
            for p in src.rglob('*'):
                if p.is_file():
                    target = ROOT / d / p.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
            # files the tests created that did not exist before stay only here
            for p in (ROOT / d).rglob('*'):
                if p.is_file() and not (src / p.relative_to(ROOT / d)).exists() and \
                        p.name != 'programs_acceptance.json':
                    rel = HERE / 'new_files' / d.replace('/', '__') / p.relative_to(ROOT / d)
                    rel.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), rel)
    summary['pass'] = ok
    (HERE / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('REGRESSION_V520_PASS' if ok else 'REGRESSION_V520_FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
