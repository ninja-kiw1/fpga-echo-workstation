"""PC baselines (no board): same integer C code on x86 + PyTorch FP32 on CPU / GPU.

  1. cpu_unet.c built in WSL with -O3 -march=native (AVX2 auto-vectorised),
     1 / 8 / 24 threads, BIT_EXACT checked on every run;
  2. PyTorch FP32 forward of the same architecture (lwnet get_arch('unet')):
     the "ordinary software deployment" reference -- float, not bit-exact.
     CPU with all threads and 1 thread; CUDA GPU with synchronisation.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe run_pc_baseline.py
"""
import json
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
PLD = HERE.parents[1]
sys.path.insert(0, str(PLD / 'lwnet'))
from models.get_model import get_arch  # noqa: E402

CKPT = {'fundus': PLD / 'lwnet/results/model_comparison/stage1_checkpoint.pth',
        'skin': PLD / 'skin/results/train/r128/best.pth'}


def wsl(cmd):
    r = subprocess.run(['wsl', '-e', 'sh', '-c', f'cd "$(wslpath -u \'{HERE}\')" && {cmd}'],
                       capture_output=True, text=True)
    return r.stdout + r.stderr, r.returncode


def time_torch(model, x, runs, device):
    with torch.inference_mode():
        model(x)                                   # warm-up
        if device == 'cuda':
            torch.cuda.synchronize()
        ts = []
        for _ in range(runs):
            t0 = time.perf_counter()
            model(x)
            if device == 'cuda':
                torch.cuda.synchronize()
            ts.append(time.perf_counter() - t0)
    ts.sort()
    return dict(min_ms=1e3 * ts[0], median_ms=1e3 * ts[len(ts) // 2], mean_ms=1e3 * float(np.mean(ts)))


def main():
    report = dict(utc=datetime.now(timezone.utc).isoformat(), c_runs=[], torch_runs=[])
    text, _ = wsl("lscpu | grep -E 'Model name|^CPU\\(s\\)|Thread|Core'; gcc --version | head -1")
    report['pc'] = dict(lscpu=text.strip(), python=platform.python_version(), torch=torch.__version__,
                        gpu=torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
    print(text, flush=True)
    text, st = wsl('make pc 2>&1')
    assert st == 0, text
    for model in ('fundus', 'skin'):
        for res, runs in ((128, 50), (512, 10)):
            for threads in (1, 8, 24):
                text, st = wsl(f'OMP_NUM_THREADS={threads} ./pc_unet_{model} vectors/{model}_{res}.rgb '
                               f'vectors/{model}_{res}.i8 {res} {res} {runs}')
                m = re.search(r'RESULT (.*)', text)
                row = dict(model=model, res=res, threads=threads, exit=st, bit_exact='BIT_EXACT' in text)
                row.update({k: (float(v) if re.fullmatch(r'[\d.]+', v) else v)
                            for k, v in (kv.split('=') for kv in m.group(1).split() if '=' in kv)})
                report['c_runs'].append(row)
                print(m.group(0), flush=True)

    for model in ('fundus', 'skin'):
        net = get_arch('unet').eval()
        net.load_state_dict(torch.load(CKPT[model], weights_only=True, map_location='cpu')['model_state_dict'])
        for res, runs in ((128, 50), (512, 10)):
            x = torch.from_numpy(np.fromfile(HERE / 'vectors' / f'{model}_{res}.rgb', np.uint8)
                                 .reshape(res, res, 3)).permute(2, 0, 1).unsqueeze(0).float() / 255
            for device, threads in (('cpu', None), ('cpu', 1), ('cuda', None)):
                if device == 'cuda' and not torch.cuda.is_available():
                    continue
                torch.set_num_threads(threads or 24)
                m = net.to(device)
                r = time_torch(m, x.to(device), runs, device)
                row = dict(model=model, res=res, device=device, threads=threads or 24, **r)
                report['torch_runs'].append(row)
                print('TORCH', row, flush=True)
            net.to('cpu')
    report['all_c_bit_exact'] = all(r['bit_exact'] and r['exit'] == 0 for r in report['c_runs'])
    (HERE / 'results').mkdir(exist_ok=True)
    (HERE / 'results' / 'pc.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('ALL_BIT_EXACT' if report['all_c_bit_exact'] else 'NOT_ALL_BIT_EXACT', flush=True)


if __name__ == '__main__':
    main()
