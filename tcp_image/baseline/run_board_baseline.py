"""Build and run the CPU baseline on the FZ3B's Cortex-A53 (PS only, PL untouched).

Uploads cpu_unet.c + makefile + net/ + vectors/ to /root/cpu_baseline, builds
natively (gcc -O3 -mcpu=cortex-a53 -fopenmp), runs every model x resolution x
thread count, checks BIT_EXACT on every run, and saves results/board_cpu.json.
Also records CPU frequency and PS/PL temperatures (AMS) before and after.

    E:\\PLD\\skin\\.venv\\Scripts\\python.exe run_board_baseline.py
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from board_sh import TCP  # noqa: F401  (sets sys.path for paramiko)
import paramiko

HERE = Path(__file__).resolve().parent
REMOTE = '/root/cpu_baseline'
RUNS = {128: 20, 512: 3}   # timed runs after one warm-up
AMS = '/sys/bus/iio/devices/iio:device0'


def main():
    c = paramiko.SSHClient()
    c.load_host_keys(str(TCP / 'tcp_image_ui' / 'board_check_v4' / 'known_hosts'))
    c.connect('192.168.1.254', username='root', password='root', timeout=10, allow_agent=False, look_for_keys=False)
    log = (HERE / 'results' / 'board_cpu.log')
    log.parent.mkdir(exist_ok=True)

    def run(cmd, timeout=1800):
        print('$', cmd, flush=True)
        _, out, _ = c.exec_command(cmd + ' 2>&1', timeout=timeout)
        text = out.read().decode('utf-8', 'replace')
        status = out.channel.recv_exit_status()
        print(text, end='', flush=True)
        with log.open('a', encoding='utf-8') as f:
            f.write(f'$ {cmd}\n{text}exit={status}\n')
        return text, status

    def env():
        t, _ = run(f'cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq; '
                   f'for s in temp0_ps temp2_pl; do r=$(cat {AMS}/in_${{s}}_temp_raw); o=$(cat {AMS}/in_${{s}}_temp_offset); '
                   f'k=$(cat {AMS}/in_${{s}}_temp_scale); echo "$r $o $k"; done; uptime')
        lines = t.split('\n')
        temps = [(int(a) + int(b)) * float(k) / 1000 for a, b, k in (l.split() for l in lines[1:3])]
        return dict(cpu_khz=int(lines[0]), ps_temp_c=round(temps[0], 1), pl_temp_c=round(temps[1], 1),
                    uptime=lines[3].strip())

    try:
        run(f'rm -rf {REMOTE} && mkdir -p {REMOTE}/net {REMOTE}/vectors')
        with c.open_sftp() as s:
            for p in [HERE / 'cpu_unet.c', HERE / 'makefile'] + sorted((HERE / 'net').iterdir()) + \
                    sorted((HERE / 'vectors').iterdir()):
                s.put(str(p), f'{REMOTE}/{p.relative_to(HERE).as_posix()}')
        _, st = run(f'cd {REMOTE} && make 2>&1 && gcc --version | head -1', 600)
        assert st == 0, 'board build failed'
        report = dict(utc=datetime.now(timezone.utc).isoformat(), env_before=env(), runs=[])
        for model in ('fundus', 'skin'):
            for res in (128, 512):
                for threads in (1, 2, 4):
                  for binary, extra in ((f'cpu_unet_{model}_plain', ''), (f'cpu_unet_{model}', ' --noblock'),
                                        (f'cpu_unet_{model}', '')):
                    text, st = run(f'cd {REMOTE} && OMP_NUM_THREADS={threads} ./{binary} '
                                   f'vectors/{model}_{res}.rgb vectors/{model}_{res}.i8 {res} {res} {RUNS[res]}{extra}')
                    m = re.search(r'RESULT (.*)', text)
                    row = dict(model=model, res=res, threads=threads, exit=st)
                    row.update({k: (float(v) if re.fullmatch(r'[\d.]+', v) else v)
                                for k, v in (kv.split('=') for kv in m.group(1).split() if '=' in kv)})
                    row['bit_exact'] = 'BIT_EXACT' in text
                    report['runs'].append(row)
        for t in (1, 4):
            text, _ = run(f'cd {REMOTE} && OMP_NUM_THREADS={t} ./cpu_unet_fundus vectors/fundus_512.rgb '
                          f'vectors/fundus_512.i8 512 512 2 --layers')
            report[f'layers_fundus_512_neon_blocked_{t}t'] = [l for l in text.split('\n') if l.startswith('LAYER')]
        report['env_after'] = env()
        report['all_bit_exact'] = all(r['bit_exact'] and r['exit'] == 0 for r in report['runs'])
        (HERE / 'results' / 'board_cpu.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
        print('ALL_BIT_EXACT' if report['all_bit_exact'] else 'NOT_ALL_BIT_EXACT', flush=True)
    finally:
        c.close()


if __name__ == '__main__':
    main()
