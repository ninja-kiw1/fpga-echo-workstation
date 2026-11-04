r"""Pre-registration of the 10-25 one-time test-set evaluations, written BEFORE any test frame is read.
Records the sha256 of every frozen input (models, deployed configs, the C the board runs, the evaluation scripts) and the
scope / metrics / order below into prereg.json. Each final-test script also writes its own lock first and refuses to
run twice. Nothing is tuned after this point; a crash is reported, not silently re-run.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe prereg.py
"""
import hashlib
import json
import os
from datetime import datetime, timezone

PLD = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
FROZEN = [
    # EchoNet model 16 + pipeline (①②③, HF classes)
    'echonet/results/quant/t3/quant_plan.json', 'echonet/results/quant/t3/parameters.npz',
    'echonet/results/beats_config_t3_int.json', 'quality/results/c/eq_model_full.json',
    'quality/results/c/eq_model_keyframe.json', 'tcp_image/tcp_image_ui/echo_kf_config.json',
    'tcp_image/tcp_image_ui/echo_heart_config.json', 'keyframe/echo_core.dll',
    'tcp_image/tcp_image_rx/src/echo_num.c', 'tcp_image/tcp_image_rx/src/kf.c', 'tcp_image/tcp_image_rx/src/echo_quality.c',
    'tcp_image/tcp_image_rx/src/cardiac_ext.c', 'tcp_image/tcp_image_rx/src/job_echo.c',
    'echonet/final_test_echonet.py', 'quality/final_test_kf.py', 'quality/hf_class.py', 'quality/quality_kf.py',
    # HMC-QU wall motion
    'wallmotion/results/split.json', 'wallmotion/results/wm_params.json', 'wallmotion/prep_hmcqu.py',
    'wallmotion/engine_masks.py', 'wallmotion/fit_wm.py', 'wallmotion/final_test.py',
    # CardiacUDA RV / RA
    'cardiac_rv/results/split.json', 'cardiac_rv/results/quant/rv_pl1_5/quant_plan.json',
    'cardiac_rv/results/quant/rv_pl1_5/parameters.npz', 'cardiac_rv/results/quant/ra_pl1_5/quant_plan.json',
    'cardiac_rv/results/quant/ra_pl1_5/parameters.npz', 'cardiac_rv/final_test_rv.py',
    'tcp_image/tcp_image_rx/programs_rh/cardiac_rv.upk', 'tcp_image/tcp_image_rx/programs_rh/cardiac_ra.upk',
    # CAMUS P1 1.6 (frozen 10-15, its own lock.json)
    'echo/final_test_p1_16.py', 'echo/results/final_test_p1_16/lock.json',
]
SCOPE = {
    'echonet_test': dict(
        data='EchoNet-Dynamic official TEST (1,277 videos), never used before',
        order=['echonet/final_test_echonet.py (its lock test_set_used.txt first): LV Dice on TEST ED / ES, beat EF '
               'full / realtime, QC — the 10-10 pre-registered evaluation, unchanged',
               'quality/echonet_q.py --split test: program 16 integer logits of every TEST frame (only after the lock)',
               'quality/final_test_kf.py: board C (echo_core.dll) full + recommended keyframe schedule; deployed '
               'calibration a/b, eq_model (q80 / q90 from VAL) — no test fitting'],
        metrics=['EF MAE / RMSE / r (full, keyframe) vs FileList EF', 'keyframe share', '80 / 90 % interval coverage',
                 'review rate', 'EF<50 and EF<=40: AUC, sens, spec, PPV, NPV (bootstrap CI)', '3-class agreement, kappa',
                 'review split, safety (called or flagged, silent misses), silent misgrades', 'by quality light']),
    'hmcqu_test': dict(
        data='HMC-QU A4C TEST split (65 recordings, wallmotion/results/split.json, 10-25 before any feature)',
        order=['wallmotion/final_test.py (lock first)'],
        metrics=['segment level: sens / spec / F1 / AUC (pooled z) and per position',
                 'recording level (MI = any segment): sens / spec / F1 / AUC of the clip score', 'LV EF of MI vs non-MI']),
    'cardiacuda_test': dict(
        data='CardiacUDA TEST (Site_G_20 + Site_R_52 even, 47 videos / 237 labelled frames)',
        order=['cardiac_rv/final_test_rv.py (lock first)'],
        metrics=['RV / RA Dice at the native crop (integer models = the board), per site, P5',
                 'plausibility rules on TEST frames vs the expert LV']),
    'camus_p1_16_test': dict(
        data='CAMUS official test split (50 patients), P1 1.6 LA / epicardium',
        order=['echo/final_test_p1_16.py --i-am-sure (its lock.json, status locked -> running -> used)'],
        metrics=['as pre-registered 10-14 in lock.json']),
}


def sha(p):
    h = hashlib.sha256()
    with open(os.path.join(PLD, p), 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prereg.json')
    if os.path.exists(out):
        raise SystemExit('prereg.json already written: ' + out)
    missing = [p for p in FROZEN if not os.path.exists(os.path.join(PLD, p))]
    if missing:
        raise SystemExit('missing frozen inputs: %s' % missing)
    rec = dict(utc=datetime.now(timezone.utc).isoformat(), rule='each test set evaluated once; nothing tuned afterwards',
               scope=SCOPE, frozen={p: sha(p) for p in FROZEN})
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    print('wrote', out, len(FROZEN), 'frozen files')


if __name__ == '__main__':
    main()
