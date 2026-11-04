"""Innovation 1 through the board C: echonet_sim.py with propagation (kf.c), volume (echo_num.c en_volume_pca) and
beats (en_analyze) taken from echo_core.dll instead of the Python prototype. Same settings, same TRAIN-subset
calibration, same VAL report; results in results/c/ (echonet_vol/*.json, echonet_summary.json). These are the
numbers the board reproduces (one source); the Python prototype numbers stay in results/ for comparison.

    C:\\Users\\21888\\anaconda3\\envs\\srtp-vision\\python.exe -I echonet_c.py run|summary
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ['PATH'] = r'C:\msys64\mingw64\bin;' + os.environ['PATH']     # MinGW runtime of echo_core.dll
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import echo_core  # noqa: E402
import echonet_sim  # noqa: E402


def propagate(frames, key_mask, policy, cfg, fps=50.0):
    km = np.stack([key_mask(i) for i in range(len(frames))])
    return echo_core.propagate(frames, km, policy, cfg, fps)


echonet_sim.kf.propagate = propagate
echonet_sim.volume_pca = echo_core.volume_pca
echonet_sim.analyze = echo_core.analyze
echonet_sim.OUT = HERE / 'results' / 'c'
echonet_sim.VOL = echonet_sim.OUT / 'echonet_vol'

if __name__ == '__main__':
    echonet_sim.OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / 'results' / 'echonet_tune.json', echonet_sim.OUT / 'echonet_tune.json')
    echonet_sim.main()
