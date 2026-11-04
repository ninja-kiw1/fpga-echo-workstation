"""Wait for the FZ3B to come back after a reboot; log ping + SSH + uname."""
from pathlib import Path
import socket
import subprocess
import time

HOST = "192.168.1.254"
LOG = str(Path(__file__).resolve().parents[1] / "boot/board_wait.log")
open(LOG, "w").close()


def log(m):
    with open(LOG, "a") as f:
        f.write(str(m) + "\n")


log("waiting for %s ..." % HOST)
up = False
for i in range(90):
    r = subprocess.run(["ping", "-n", "1", "-w", "1000", HOST],
                       capture_output=True, text=True)
    ping_ok = "TTL=" in r.stdout or "TTL=" in r.stderr
    ssh_ok = False
    if ping_ok:
        try:
            s = socket.create_connection((HOST, 22), timeout=2)
            s.close()
            ssh_ok = True
        except Exception:
            ssh_ok = False
    log("%3d  ping=%-5s ssh22=%-5s" % (i, ping_ok, ssh_ok))
    if ping_ok and ssh_ok:
        up = True
        break
    time.sleep(3)

if up:
    import paramiko
    try:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(HOST, username="root", password="root", timeout=20,
                    allow_agent=False, look_for_keys=False)
        for cmd in ("uname -a", "cat /proc/uptime", "cat /proc/cmdline",
                    "ls -l /run/media/mmcblk1p1/boot.bin"):
            _i, o, e = cli.exec_command(cmd, timeout=30)
            log("$ %s\n%s" % (cmd, o.read().decode("utf-8", "replace").rstrip()))
        cli.close()
    except Exception as ex:
        log("ssh err: %r" % (ex,))
log("BOARD_WAIT_DONE up=%s" % up)
