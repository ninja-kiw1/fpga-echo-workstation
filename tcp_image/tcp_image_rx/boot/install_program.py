"""Install / remove program packages (*.upk) on the board without restarting the server.

The server (v5.9+, src/progpkg.c) loads every *.upk in /root/tcp_image_rx/programs at
start-up and again on SIGHUP; each package is self-tested on the PL and only
registered if its logits match bit for bit. The result of the last load is
<dir>/LOADED.txt, which this tool waits for and prints.

    python install_program.py install a.upk [b.upk ...]   # upload, reload, require "OK" for each
    python install_program.py remove a.upk [...]           # delete, reload
    python install_program.py list                         # LOADED.txt of the last load
    python install_program.py reload

Needs paramiko from tcp_image/.board_tools (any Python 3 with cffi for it, e.g. anaconda base).
"""
import posixpath
import sys
import time
from pathlib import Path

RX = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RX.parent / ".board_tools"))
import paramiko  # noqa: E402

HOST = "192.168.1.254"
DIR = "/root/tcp_image_rx/programs"
LOADED = DIR + "/LOADED.txt"


def connect():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password="root", timeout=10, allow_agent=False, look_for_keys=False)
    return c


def run(c, cmd, timeout=60):
    _, out, _ = c.exec_command(cmd + " 2>&1", timeout=timeout)
    text = out.read().decode("utf-8", "replace")
    status = out.channel.recv_exit_status()
    if status:
        raise RuntimeError("remote exit %d: %s\n%s" % (status, cmd, text))
    return text


def reload(c, wait_s=30.0):
    """SIGHUP the server and wait until LOADED.txt is rewritten (the server reloads between frames)."""
    run(c, "rm -f " + LOADED + " && systemctl kill -s HUP tcp-image-server")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(0.5)
        if run(c, "test -f " + LOADED + " && echo yes || echo no").strip() == "yes":
            break
    else:
        print("[install] LOADED.txt did not change within %.0f s (a client may be holding the "
              "connection; the server reloads after its next frame or the 10 s idle timeout)" % wait_s)
    time.sleep(0.3)
    return run(c, "cat " + LOADED + " 2>/dev/null || true")


def table(text):
    rows = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            f = line.split("\t")
            rows[f[0]] = f
    return rows


def main(argv):
    if not argv or argv[0] not in ("install", "remove", "list", "reload"):
        print(__doc__)
        return 2
    action, files = argv[0], argv[1:]
    c = connect()
    try:
        run(c, "mkdir -p " + DIR)
        if action == "install":
            with c.open_sftp() as s:
                for f in files:
                    final = posixpath.join(DIR, Path(f).name)
                    s.put(f, final + ".part")              # the server only scans *.upk
                    run(c, "mv -f %s.part %s" % (final, final))
        elif action == "remove":
            for f in files:
                run(c, "rm -f " + posixpath.join(DIR, Path(f).name))
        text = run(c, "cat " + LOADED + " 2>/dev/null || true") if action == "list" else reload(c)
        print(text, end="")
        if action == "install":
            rows = table(text)
            bad = [Path(f).name for f in files
                   if Path(f).name not in rows or not rows[Path(f).name][4].startswith("OK")]
            if bad:
                print("[install] NOT loaded: %s" % ", ".join(bad))
                return 1
            print("INSTALL_PROGRAM_PASS")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
