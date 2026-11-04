"""perf_j board runner: upload sources to /root/perf_j, run commands with the TCP
server stopped and the BoardCheck GUI paused, always restore both afterwards.

  python run_board.py upload FILE...          # copy to /root/perf_j
  python run_board.py excl LABEL "CMD" [TMO]  # server stopped for CMD, log -> results/LABEL.log
  python run_board.py plain LABEL "CMD" [TMO] # server left running
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

from bt import HERE, connect, run

REMOTE = "/root/perf_j"
GUI = ('pids=$(pgrep -f "^python3 /root/Fz3b_BoardCheck/gui/board_check_gui.py$" || true); '
       'if [ -n "$pids" ]; then kill -{sig} $pids; fi')
RES = HERE / "results"


def log(label, text):
    RES.mkdir(exist_ok=True)
    with open(RES / f"{label}.log", "a", encoding="utf-8") as f:
        f.write(text)


def main():
    act = sys.argv[1]
    c = connect()
    if act == "upload":
        run(c, f"mkdir -p {REMOTE}")
        s = c.open_sftp()
        for p in sys.argv[2:]:
            s.put(str(Path(p)), f"{REMOTE}/{Path(p).name}")
            print("put", p)
        return 0
    label, cmd = sys.argv[2], sys.argv[3]
    tmo = int(sys.argv[4]) if len(sys.argv) > 4 else 900
    stamp = datetime.now(timezone.utc).isoformat()
    log(label, f"=== PC_UTC {stamp}\n$ {cmd}\n")
    rc = 1
    if act == "excl":
        t, rc0 = run(c, 'set -eu; systemctl stop tcp-image-server; '
                     'test -z "$(pgrep -x tcp_image_serve || true)"; ' + GUI.format(sig="STOP"))
        if rc0:
            print("could not stop server")
            return 1
    try:
        text, rc = run(c, f"cd {REMOTE} && {cmd}", timeout=tmo)
        log(label, text + f"\nexit={rc}\n")
    finally:
        if act == "excl":
            t, rc1 = run(c, "pkill -x hog; systemctl start tcp-image-server; sleep 1; "
                         "systemctl is-active tcp-image-server; " + GUI.format(sig="CONT"))
            log(label, "restore: " + t)
    return rc


if __name__ == "__main__":
    sys.exit(main())
