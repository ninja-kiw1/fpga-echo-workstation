r"""tcp_image_ui/echo_heart_config.json: the HC01 numbers (src/cardiac_ext.h) the window sends with echo_ef mode 2 / 3.
wall_motion from results/wm_params.json (HMC-QU DEV only); right_heart = programs 21 cardiac_rv / 22 cardiac_ra
(E:/PLD/cardiac_rv, packages tcp_image_rx/programs_rh) with the cross_check.py plausibility thresholds.

    C:\Users\21888\anaconda3\envs\srtp-vision\python.exe make_heart_config.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
wm = json.load(open(os.path.join(HERE, 'results', 'wm_params.json')))
out = dict(
    wall_motion=dict(wm['params'], segments=wm['segments'],
                     names=['基底段间隔', '中段间隔', '心尖段间隔', '心尖段侧壁', '中段侧壁', '基底段侧壁'],
                     source='E:/PLD/wallmotion (HMC-QU A4C DEV %d recordings, fit_wm.py)' % wm['dev_n'],
                     dev_cv=dict(segment=wm['dev_cv']['segment'], recording=wm['dev_cv']['recording'])),
    right_heart=dict(rv_model=21, ra_model=22, min_frac=0.15, max_overlap=0.1,
                     source='E:/PLD/cardiac_rv (CardiacUDA rv_pl1_5 / ra_pl1_5; rules = cross_check.py)',
                     reference=dict(rv_fac_low=35.0, rv_lv_high=1.0,
                                    note='RV FAC < 35 % 与 RV/LV 舒张末面积比 > 1.0 仅作复核提示参考（ASE 2015 RV FAC；'
                                         'RV/LV 比为面积比，非指南直径比）')),
    wording='辅助测量与复核提示，不作诊断')
p = os.path.join(HERE, '..', 'tcp_image', 'tcp_image_ui', 'echo_heart_config.json')
json.dump(out, open(p, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
print('wrote', os.path.normpath(p))
