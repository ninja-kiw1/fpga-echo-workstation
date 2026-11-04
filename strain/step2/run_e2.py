"""Part E step 2 board micro-benchmark (no deployment): upload sources + step-1 VAL data to /root/strain_e2,
then with the TCP server stopped and the BoardCheck GUI paused: compile (board gcc), bit-exact test (C + NEON vs
the step-1 reference), single-core benchmark; always restart the server / resume the GUI afterwards.

    python run_e2.py [LABEL]    -> log in results/LABEL.log (default board_e2 = run 1, 10-17; run 2: board_e2b)
Same SSH helper and stop / restore sequence as perf_j/run_board.py.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "perf_j"))
from bt import connect, run  # noqa: E402

REMOTE = "/root/strain_e2"
DATA = HERE.parent / "step1" / "data"
GUI = ('pids=$(pgrep -f "^python3 /root/Fz3b_BoardCheck/gui/board_check_gui.py$" || true); '
       'if [ -n "$pids" ]; then kill -{sig} $pids; fi')
CC = "gcc -O3 -mcpu=cortex-a53 -std=gnu99 -Wall"
CMD = " && ".join([
    "gcc --version | head -1", "uname -a",
    "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq "
    "/sys/devices/system/cpu/cpu0/cpufreq/scaling_available_frequencies 2>/dev/null || true",
    f"{CC} -o test_bm test_bm.c bm_sad.c",
    f"{CC} -o bench_bm bench_bm.c bm_sad.c -lm",
    f"{CC} -fno-tree-vectorize -o bench_bm_novec bench_bm.c bm_sad.c -lm",
    "./test_bm data",
    "echo '== bench -O3'", "taskset -c 1 ./bench_bm data 2",
    "echo '== bench -O3 -fno-tree-vectorize'", "taskset -c 1 ./bench_bm_novec data 1",
    "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || true",
])


def main():
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    logf = out / f"{sys.argv[1] if len(sys.argv) > 1 else 'board_e2'}.log"
    c = connect()
    run(c, f"mkdir -p {REMOTE}/data")
    s = c.open_sftp()
    for p in ("bm_sad.h", "bm_sad.c", "test_bm.c", "bench_bm.c"):
        s.put(str(HERE / p), f"{REMOTE}/{p}")
    for p in sorted(DATA.iterdir()):
        s.put(str(p), f"{REMOTE}/data/{p.name}")
    print("uploaded")
    with open(logf, "a", encoding="utf-8") as f:
        f.write(f"=== PC_UTC {datetime.now(timezone.utc).isoformat()}\n$ {CMD}\n")
    t, rc0 = run(c, 'set -eu; systemctl stop tcp-image-server; '
                 'test -z "$(pgrep -x tcp_image_serve || true)"; ' + GUI.format(sig="STOP"))
    if rc0:
        print("could not stop server")
        return 1
    rc = 1
    try:
        text, rc = run(c, f"cd {REMOTE} && {CMD}", timeout=900)
        with open(logf, "a", encoding="utf-8") as f:
            f.write(text + f"\nexit={rc}\n")
    finally:
        t, rc1 = run(c, "systemctl start tcp-image-server; sleep 1; systemctl is-active tcp-image-server; "
                     + GUI.format(sig="CONT"))
        with open(logf, "a", encoding="utf-8") as f:
            f.write("restore: " + t)
    return rc


if __name__ == "__main__":
    sys.exit(main())
