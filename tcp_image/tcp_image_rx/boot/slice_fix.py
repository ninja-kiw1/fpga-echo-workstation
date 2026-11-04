"""Give tcp-image-server its own high-share CPU slice (10-20, plan 心脏聚焦方案 §4 F1).

Why: cgroup v1 cpu controller on the board has /system.slice and /user.slice, both cpu.shares 1024.
The server lives in system.slice, ssh-started load in user.slice, so CFS splits CPU between the two
slices first and the unit's Nice=-20 only acts inside system.slice (perf_j/j3_results_f150_2026-10-17.md:
deployed 128 net under cpu4 load P50/P99 35.9/183.8 ms vs 22.05/22.17 idle).

Fix: a top-level slice tcpimage.slice (no dash: a dash would make it a child of tcp.slice) with
CPUShares=8192, and a drop-in 20-slice.conf putting the service there. Independent of
deploy_board_v5.py (which only manages 10-nice.conf), so server releases/rollbacks leave it alone.

    python slice_fix.py status | apply | rollback
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / ".board_tools"))
import paramiko  # noqa: E402

SHARES = 8192
SLICE_REMOTE = "/etc/systemd/system/tcpimage.slice"
DROPIN_REMOTE = "/etc/systemd/system/tcp-image-server.service.d/20-slice.conf"
SLICE_TEXT = """# Installed by tcp_image_rx/boot/slice_fix.py (2026-10-20). Rollback: slice_fix.py rollback.
[Unit]
Description=tcp_image server slice (CPU share above user.slice ssh load)
Before=slices.target

[Slice]
CPUAccounting=yes
CPUShares=%d
""" % SHARES
DROPIN_TEXT = """# Installed by tcp_image_rx/boot/slice_fix.py (2026-10-20). Rollback: slice_fix.py rollback.
# Own top-level slice so CFS gives the server 8192 shares against user.slice's 1024.
[Service]
Slice=tcpimage.slice
"""
STATUS = ("systemctl show -p Slice -p ControlGroup -p Nice -p ActiveState tcp-image-server; "
          "for g in system.slice user.slice tcpimage.slice; do "
          "f=/sys/fs/cgroup/cpu/$g/cpu.shares; test -f $f && echo $g $(cat $f); done; "
          "p=$(systemctl show -p MainPID --value tcp-image-server); echo pid $p; grep cpu, /proc/$p/cgroup")


def run(c, cmd):
    _, out, _ = c.exec_command(cmd + " 2>&1", timeout=120)
    text = out.read().decode("utf-8", "replace")
    if out.channel.recv_exit_status():
        raise RuntimeError(cmd + "\n" + text)
    return text


def put(c, path, text):
    s = c.open_sftp()
    try:
        with s.open(path, "w") as f:
            f.write(text)
    finally:
        s.close()


def main(action):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect("192.168.1.254", username="root", password="root", timeout=10,
              allow_agent=False, look_for_keys=False)
    try:
        if action == "apply":
            run(c, "mkdir -p $(dirname %s)" % DROPIN_REMOTE)
            put(c, SLICE_REMOTE, SLICE_TEXT)
            put(c, DROPIN_REMOTE, DROPIN_TEXT)
            run(c, "systemctl daemon-reload && systemctl restart tcp-image-server && sleep 4")
        elif action == "rollback":
            run(c, "rm -f %s %s && systemctl daemon-reload && systemctl restart tcp-image-server && sleep 4"
                % (DROPIN_REMOTE, SLICE_REMOTE))
        text = run(c, STATUS)
        print(text)
        if action == "apply" and ("Slice=tcpimage.slice" not in text or "tcpimage.slice %d" % SHARES not in text
                                  or "Nice=-20" not in text or "ActiveState=active" not in text):
            raise SystemExit("SLICE_FIX_NOT_EFFECTIVE")
        if action == "rollback" and "Slice=system.slice" not in text:
            raise SystemExit("ROLLBACK_NOT_EFFECTIVE")
    finally:
        c.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "status")
