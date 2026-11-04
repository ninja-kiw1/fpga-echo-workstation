"""Upload a local script to the board and run it with python3.

Usage: board_put_run.py <local_file> <remote_path> [args...]
"""
import sys

import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"

local = sys.argv[1]
remote = sys.argv[2]
args = sys.argv[3:] if len(sys.argv) > 3 else []

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)
sftp = cli.open_sftp()
sftp.put(local, remote)
sftp.close()

cmd = "python3 %s %s" % (remote, " ".join(args))
_in, out, err = cli.exec_command(cmd, timeout=120)
o = out.read().decode("utf-8", errors="replace").rstrip()
e = err.read().decode("utf-8", errors="replace").rstrip()
cli.close()

print("--- stdout ---")
print(o if o else "(empty)")
if e:
    print("--- stderr ---")
    print(e)
