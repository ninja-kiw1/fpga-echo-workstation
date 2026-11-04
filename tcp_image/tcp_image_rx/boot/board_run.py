"""Run an arbitrary shell command on the FZ3B board over SSH and print the output.

Usage:  python board_run.py "<command>"

Used as a general-purpose remote exec helper (the sandbox has no direct network
access, so all board I/O goes through the host python + paramiko).
"""
import sys

import paramiko

HOST = "192.168.1.254"
USER = "root"
PWD = "root"

if len(sys.argv) < 2:
    print("usage: board_run.py '<command>'")
    sys.exit(1)

cmd = sys.argv[1]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PWD, timeout=20,
            allow_agent=False, look_for_keys=False)
try:
    _in, out, err = cli.exec_command(cmd, timeout=120)
    o = out.read().decode("utf-8", errors="replace").rstrip()
    e = err.read().decode("utf-8", errors="replace").rstrip()
finally:
    cli.close()

print("--- stdout ---")
print(o if o else "(empty)")
if e:
    print("--- stderr ---")
    print(e)
