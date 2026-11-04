"""Minimal serial SSH helper for perf_j (paramiko from tcp_image/.board_tools)."""
import sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tcp_image/.board_tools"))
import paramiko  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def connect():
    c = paramiko.SSHClient()
    c.load_host_keys(str(HERE / "known_hosts"))
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10,
              banner_timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
    c.get_transport().set_keepalive(10)
    return c


def run(c, cmd, timeout=600, log=None, echo=True):
    ch = c.get_transport().open_session(timeout=10)
    ch.set_combine_stderr(True)
    ch.exec_command(cmd)
    buf = bytearray()
    t_end = time.monotonic() + timeout
    while True:
        if ch.recv_ready():
            d = ch.recv(65536)
            buf += d
            if echo:
                print(d.decode("utf-8", "replace"), end="", flush=True)
        elif ch.exit_status_ready() and not ch.recv_ready():
            break
        else:
            if time.monotonic() > t_end:
                ch.close()
                raise TimeoutError(cmd)
            time.sleep(0.05)
    rc = ch.recv_exit_status()
    text = buf.decode("utf-8", "replace")
    if log:
        with open(HERE / log, "a", encoding="utf-8") as f:
            f.write(f"$ {cmd}\n{text}\nexit={rc}\n")
    return text, rc


if __name__ == "__main__":
    c = connect()
    t, rc = run(c, sys.argv[1], log=sys.argv[2] if len(sys.argv) > 2 else None)
    sys.exit(rc)
