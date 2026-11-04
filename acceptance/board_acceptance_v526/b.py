"""Small board SSH helper for the r1 (resource optimisation) round.

  python b.py run "<cmd>" [timeout_s]
  python b.py get <remote> <local>        (file or directory, recursive)
  python b.py put <local> <remote>
"""
import os
import stat
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tcp_image/.board_tools"))
import paramiko  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOST = "192.168.1.254"


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password="root", timeout=10,
              banner_timeout=10, auth_timeout=10, allow_agent=False, look_for_keys=False)
    c.get_transport().set_keepalive(10)
    return c


def run(c, cmd, timeout=600):
    ch = c.get_transport().open_session(timeout=10)
    ch.set_combine_stderr(True)
    ch.exec_command(cmd)
    end = time.monotonic() + timeout
    while True:
        if ch.recv_ready():
            sys.stdout.write(ch.recv(65536).decode("utf-8", "replace"))
            sys.stdout.flush()
        elif ch.exit_status_ready():
            while ch.recv_ready():
                sys.stdout.write(ch.recv(65536).decode("utf-8", "replace"))
            break
        elif time.monotonic() > end:
            ch.close()
            raise TimeoutError(cmd)
        else:
            time.sleep(0.05)
    return ch.recv_exit_status()


def get(sftp, remote, local):
    st = sftp.stat(remote)
    if stat.S_ISDIR(st.st_mode):
        os.makedirs(local, exist_ok=True)
        for e in sftp.listdir_attr(remote):
            get(sftp, remote + "/" + e.filename, os.path.join(local, e.filename))
    else:
        os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
        sftp.get(remote, local)


def main():
    a = sys.argv[1:]
    c = connect()
    try:
        if a[0] == "run":
            rc = run(c, a[1], int(a[2]) if len(a) > 2 else 600)
            print(f"\n[rc={rc}]")
            sys.exit(rc)
        sftp = c.open_sftp()
        if a[0] == "get":
            get(sftp, a[1], a[2])
        elif a[0] == "put":
            sftp.put(a[1], a[2])
        print("ok")
    finally:
        c.close()


if __name__ == "__main__":
    main()
