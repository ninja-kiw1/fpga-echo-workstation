"""Hold the FZ3B in fixed workload phases so an external power meter can be read.

The board has no current sensor (only AMS voltages / temperatures), so power
must come from a meter in series with the board's DC input. This script only
creates the workloads and timestamps them; you read the meter (or let a
logging meter record) during each phase and type the average watts in at the
prompt (or press Enter to skip). Results: results/power_<utc>.json.

Phases (default 60 s each, fundus server must be the active service):
  idle         server idle, nothing running
  fpga_512     PC streams frames, board runs the whole U-Net on PL at 512x512
               (flags: stats + no-image, so the network link is nearly idle)
  fpga_128     same at 128x128
  cpu4_512     board CPU baseline, 4 threads NEON, 512x512 (PL idle)
  cpu1_512     board CPU baseline, 1 thread NEON, 512x512
Energy per frame = average power x frame time (also reported above idle).

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe measure_power_phases.py [--seconds 60] [--no-prompt]
"""
import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from board_sh import TCP, run

sys.path.insert(0, str(TCP / 'tcp_image_ui'))
import imagelink  # noqa: E402
import proto      # noqa: E402

HERE = Path(__file__).resolve().parent
AMS = '/sys/bus/iio/devices/iio:device0'


def ams():
    t, _ = run(f'for s in temp0_ps temp2_pl; do echo $(cat {AMS}/in_${{s}}_temp_raw) $(cat {AMS}/in_${{s}}_temp_offset) '
               f'$(cat {AMS}/in_${{s}}_temp_scale); done; cat /proc/loadavg')
    lines = t.strip().split('\n')
    temps = [(int(a) + int(b)) * float(k) / 1000 for a, b, k in (l.split() for l in lines[:2])]
    return dict(ps_temp_c=round(temps[0], 1), pl_temp_c=round(temps[1], 1), loadavg=lines[2])


def fpga_phase(seconds, res):
    rgb = (HERE / 'vectors' / 'fundus_512.rgb').read_bytes()
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=20)
    flags = proto.FLAG_STATS | proto.FLAG_NO_IMAGE
    ai, n, t0 = [], 0, time.time()
    while time.time() - t0 < seconds:
        _, _, _, st = link.round_trip(rgb, mode=0, flags=flags, aiparam=proto.make_aiparam(0, -3, res))
        ai.append(st['ai_us'] / 1000)
        n += 1
    link.close()
    wall = time.time() - t0
    return dict(frames=n, fps=n / wall, ai_ms_mean=sum(ai) / len(ai), frame_ms=1000 * wall / n)


def cpu_phase(seconds, threads):
    per = {4: 0.57, 1: 2.25}[threads]                   # s per 512 frame, measured 10-08
    runs = max(3, int(seconds / per))
    t0 = time.time()
    text, st = run(f'cd /root/cpu_baseline && OMP_NUM_THREADS={threads} ./cpu_unet_fundus '
                   f'vectors/fundus_512.rgb vectors/fundus_512.i8 512 512 {runs}', timeout=seconds * 4 + 120)
    wall = time.time() - t0
    line = next(l for l in text.split('\n') if l.startswith('RESULT'))
    mean_ms = float(line.split('mean_ms=')[1].split()[0])
    return dict(frames=runs + 1, frame_ms=mean_ms, fps=1000 / mean_ms, bit_exact='BIT_EXACT' in line,
                wall_s=wall, result=line)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--seconds', type=int, default=60)
    ap.add_argument('--no-prompt', action='store_true', help='dry run: no meter readings asked')
    args = ap.parse_args()
    state, _ = run('systemctl is-active tcp-image-server')
    assert state.strip() == 'active', 'switch the board to the fundus server first'
    phases = [('idle', lambda: (time.sleep(args.seconds), {})[1]),
              ('fpga_512', lambda: fpga_phase(args.seconds, 512)),
              ('fpga_128', lambda: fpga_phase(args.seconds, 128)),
              ('cpu4_512', lambda: cpu_phase(args.seconds, 4)),
              ('cpu1_512', lambda: cpu_phase(args.seconds, 1))]
    report = dict(utc=datetime.now(timezone.utc).isoformat(), seconds=args.seconds, phases=[])
    for name, work in phases:
        print(f'\n=== {name}: starts now, about {args.seconds} s -- read the meter during this phase', flush=True)
        t0 = datetime.now().strftime('%H:%M:%S')
        result = {}
        worker = threading.Thread(target=lambda: result.update(work() or {}))
        worker.start()
        worker.join()
        row = dict(phase=name, start=t0, end=datetime.now().strftime('%H:%M:%S'), **result, **ams())
        if not args.no_prompt:
            watts = input(f'{name}: average watts on the meter (Enter = skip): ').strip()
            row['watts'] = float(watts) if watts else None
        report['phases'].append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        time.sleep(5)                                    # settle between phases
    idle = next((p.get('watts') for p in report['phases'] if p['phase'] == 'idle'), None)
    for p in report['phases']:
        if p.get('watts') and p.get('frame_ms'):
            p['joule_per_frame'] = p['watts'] * p['frame_ms'] / 1000
            if idle:
                p['joule_per_frame_above_idle'] = (p['watts'] - idle) * p['frame_ms'] / 1000
    out = HERE / 'results' / f"power_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    print('saved', out)


if __name__ == '__main__':
    main()
