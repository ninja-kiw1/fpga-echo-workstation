# -*- coding: utf-8 -*-
"""极简远程命令执行器: python rshell.py "<remote command>"

供开发时在主机上跑板端命令用(避免 Tcl exec 的引号地狱)。
"""
import sys
import paramiko

HOST, USER, PW = "192.168.1.254", "root", "root"


def run(cmd, timeout=60):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PW, timeout=15,
              look_for_keys=False, allow_agent=False)
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors="replace")
    err = e.read().decode(errors="replace")
    c.close()
    return out, err


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--b64":
        import base64
        cmd = base64.b64decode(args[1]).decode()
    else:
        cmd = " ".join(args)
    o, e = run(cmd)
    sys.stdout.write(o)
    if e.strip():
        sys.stdout.write("[stderr] " + e)
